"""Modal controller for frame-bounded Blender raw-pass rendering."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from enum import StrEnum
from typing import Any, Protocol

from .modal_lifecycle import ModalOperationLifecycle, OperationPhase, RuntimeState
from .raw_render import RenderFrameRequest


class RenderEvent(StrEnum):
    """Observable state of the adapter's currently launched render."""

    NONE = "NONE"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class StatusSettings(Protocol):
    """Mutable user-visible status required from scene settings."""

    status: str


class ReportingOperator(Protocol):
    """Blender operator reporting surface."""

    def report(self, type: Any, message: str) -> None: ...


class RenderSession(Protocol):
    """Incremental raw-render session consumed by the modal controller."""

    frame_start: int
    frame_end: int
    current_frame: int

    @property
    def completed_frames(self) -> tuple[object, ...]: ...

    @property
    def is_finished(self) -> bool: ...

    def prepare_next_frame(self) -> RenderFrameRequest: ...

    def complete_frame(self, request: RenderFrameRequest) -> object: ...

    def close(self) -> None: ...


class RenderAdapter(Protocol):
    """Render-event boundary implemented by Blender and deterministic test adapters."""

    def launch(self, request: RenderFrameRequest, run_identity: str) -> None: ...

    def poll(self) -> RenderEvent: ...

    def remove(self) -> None: ...


class RawRenderModalController:
    """Drive one raw frame render at a time from Blender modal timer events."""

    def __init__(
        self,
        operator: ReportingOperator,
        runtime: RuntimeState,
        settings: StatusSettings,
        *,
        adapter: RenderAdapter,
        on_cleanup: Callable[[], None] | None = None,
        run_identity_factory: Callable[[], str] | None = None,
        lifecycle: ModalOperationLifecycle | None = None,
        on_complete: Callable[[RenderSession], set[Any]] | None = None,
        on_cancelled: Callable[[int], set[Any]] | None = None,
        on_failed: Callable[[int, Exception], set[Any]] | None = None,
    ) -> None:
        self._operator = operator
        self._runtime = runtime
        self._settings = settings
        self._adapter = adapter
        self._on_cleanup = on_cleanup
        self._on_complete = on_complete
        self._on_cancelled = on_cancelled
        self._on_failed = on_failed
        self._session: RenderSession | None = None
        self._active_request: RenderFrameRequest | None = None
        self._cancel_requested = False
        self._finalized = False
        self._lifecycle = lifecycle or ModalOperationLifecycle(
            operator,
            runtime,
            cleanup=self._cleanup,
            run_identity_factory=run_identity_factory,
        )

    def attach(self, session: RenderSession) -> None:
        """Attach a session to a lifecycle initialized by a composing workflow."""
        if self._session is not None:
            raise RuntimeError("A raw-render session is already attached")
        self._session = session

    def start(self, context: Any, session: RenderSession) -> None:
        """Acquire modal resources and expose the first render boundary."""
        self.attach(session)
        total = session.frame_end - session.frame_start + 1
        self._lifecycle.begin(
            context,
            frame_start=session.frame_start,
            frame_end=session.frame_end,
            total_work=total,
        )
        self._lifecycle.update(
            phase=OperationPhase.RENDERING,
            current_frame=session.current_frame,
            completed_work=len(session.completed_frames),
            status=f"Ready to render frame {session.current_frame} of {session.frame_end}",
        )
        self._lifecycle.enter_modal()

    def handle_event(self, event: Any) -> set[Any]:
        """Launch or observe at most one frame-render boundary per timer event."""
        if event.type == "ESC":
            self.request_cancel()
            return {"RUNNING_MODAL"}
        if event.type != "TIMER" or not self._lifecycle.accepts_timer_event(event):
            return {"PASS_THROUGH"}
        session = self._session
        if session is None:
            return {"CANCELLED"}
        if self._active_request is None:
            if self.cancel_requested:
                return self._finish_cancelled()
            frame_number = session.current_frame
            try:
                expected_identity = self._runtime.run_identity
                request = session.prepare_next_frame()
                if self.cancel_requested:
                    return self._finish_cancelled()
                if self._runtime.run_identity != expected_identity:
                    raise RuntimeError("Raw render run ownership changed during frame preparation")
                self._active_request = request
                self._adapter.launch(request, expected_identity)
            except Exception as error:
                return self._fail(frame_number, error)
            # EXEC_DEFAULT is synchronous in the production adapter. Consume and verify its
            # terminal event without reapplying timer admission; asynchronous fakes remain pollable.
            return self._observe_active_render(session)

        return self._observe_active_render(session)

    def _observe_active_render(self, session: RenderSession) -> set[Any]:
        request = self._active_request
        if request is None:
            return {"RUNNING_MODAL"}
        try:
            adapter_event = self._adapter.poll()
        except Exception as error:
            return self._fail(request.frame, error)
        if adapter_event in {RenderEvent.NONE, RenderEvent.ACTIVE}:
            return {"RUNNING_MODAL"}
        if adapter_event is RenderEvent.COMPLETED:
            try:
                session.complete_frame(request)
                self._adapter.remove()
                self._active_request = None
                current_frame = min(session.current_frame, session.frame_end)
                message = (
                    f"Rendered frame {request.frame} of {session.frame_end}"
                    if not session.is_finished
                    else f"Rendered {len(session.completed_frames)} raw frame(s)"
                )
                self._lifecycle.update(
                    phase=OperationPhase.RENDERING,
                    current_frame=current_frame,
                    completed_work=len(session.completed_frames),
                    status=message,
                )
            except Exception as error:
                return self._fail(request.frame, error)
            if self.cancel_requested:
                return self._finish_cancelled()
            if not session.is_finished:
                return {"RUNNING_MODAL"}
            if self._on_complete is not None:
                return self._on_complete(session)
            if not self._finalize(OperationPhase.COMPLETED, message):
                self._operator.report({"ERROR"}, self._visible_status(message))
                return {"CANCELLED"}
            self._operator.report({"INFO"}, message)
            return {"FINISHED"}
        if adapter_event is RenderEvent.CANCELLED:
            cancelled_frame = request.frame
            try:
                self._adapter.remove()
                self._active_request = None
            except Exception as error:
                return self._fail(cancelled_frame, error)
            return self._finish_cancelled()
        try:
            error = getattr(self._adapter, "error", None)
        except Exception as adapter_error:
            error = adapter_error
        return self._fail(
            request.frame,
            error if isinstance(error, Exception) else RuntimeError("Blender render failed"),
        )

    def cancel(self) -> None:
        """Release owned resources when Blender cancels the operator externally."""
        if not self._finalized:
            self._finish_cancelled()

    def fail_initialization(self, frame_number: int, error: Exception) -> None:
        """Publish setup failure through the same idempotent lifecycle cleanup."""
        if self._finalized:
            return
        message = f"Raw rendering failed during initialization at frame {frame_number}: {error}"
        self._finalize(OperationPhase.FAILED, message)
        self._operator.report({"ERROR"}, self._visible_status(message))

    @property
    def cancel_requested(self) -> bool:
        """Whether cancellation is pending in controller-owned or scene-visible state."""
        if self._cancel_requested:
            return True
        try:
            return self._runtime.cancel_requested
        except Exception:
            return False

    def request_cancel(self) -> bool:
        """Publish cancellation without relying on the initiating scene remaining valid."""
        if self._finalized:
            return False
        if self.cancel_requested:
            return True
        self._cancel_requested = True
        with suppress(Exception):
            self._lifecycle.request_cancel()
        return True

    def _fail(self, frame_number: int, error: Exception) -> set[Any]:
        if self._on_failed is not None:
            return self._on_failed(frame_number, error)
        message = f"Raw rendering failed during rendering at frame {frame_number}: {error}"
        self._finalize(OperationPhase.FAILED, message)
        self._operator.report({"ERROR"}, self._visible_status(message))
        return {"CANCELLED"}

    def _finish_cancelled(self) -> set[Any]:
        session = self._session
        completed = len(session.completed_frames) if session is not None else 0
        if self._on_cancelled is not None:
            return self._on_cancelled(completed)
        message = f"Cancelled after {completed} frame(s)"
        cleanup_succeeded = self._finalize(OperationPhase.CANCELLED, message)
        level = {"WARNING"} if cleanup_succeeded else {"ERROR"}
        self._operator.report(level, self._visible_status(message))
        return {"CANCELLED"}

    def _finalize(self, phase: OperationPhase, status: str) -> bool:
        self._finalized = True
        succeeded = True
        try:
            self._lifecycle.finalize(phase, status)
        except Exception:
            succeeded = False
        visible = self._visible_status(status)
        with suppress(Exception):
            self._settings.status = visible
        return succeeded

    def _visible_status(self, fallback: str) -> str:
        try:
            return self._runtime.status
        except Exception:
            return fallback

    def _cleanup(self) -> None:
        cleanup_errors: list[Exception] = []
        try:
            self._adapter.remove()
        except Exception as error:
            cleanup_errors.append(error)

        session, self._session = self._session, None
        if session is not None:
            try:
                session.close()
            except Exception as error:
                cleanup_errors.append(error)

        if self._on_cleanup is not None:
            try:
                self._on_cleanup()
            except Exception as error:
                cleanup_errors.append(error)

        if len(cleanup_errors) == 1:
            raise cleanup_errors[0]
        if cleanup_errors:
            raise RuntimeError("; ".join(str(error) for error in cleanup_errors))
