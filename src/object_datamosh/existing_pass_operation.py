"""Modal controller for processing existing pass sequences.

This module owns the operation state machine without defining Blender RNA types. The UI operator
constructs the processing session and delegates event handling here.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import Any, Protocol

from .modal_lifecycle import ModalOperationLifecycle, OperationPhase, RuntimeState
from .sequence_processing import ProcessingSession, SequenceProcessingCancelled


class StatusSettings(Protocol):
    """Mutable user-visible status required from the scene settings."""

    status: str


class ReportingOperator(Protocol):
    """Blender operator surface used for user-visible reports."""

    def report(self, type: Any, message: str) -> None: ...


class ExistingPassModalController:
    """Drive one processing session through bounded timer steps and terminal cleanup."""

    def __init__(
        self,
        operator: ReportingOperator,
        runtime: RuntimeState,
        settings: StatusSettings,
        *,
        on_cleanup: Callable[[], None] | None = None,
    ) -> None:
        self._operator = operator
        self._runtime = runtime
        self._settings = settings
        self._on_cleanup = on_cleanup
        self._session: ProcessingSession | None = None
        self._cancel_requested = False
        self._finalized = False
        self._lifecycle = ModalOperationLifecycle(
            operator,
            runtime,
            cleanup=self._cleanup_session,
        )

    def start(self, context: Any, session: ProcessingSession) -> None:
        """Install modal resources and publish the session's initial work boundary."""
        self._session = session
        total_work = session.frame_end - session.frame_start + 1
        self._lifecycle.begin(
            context,
            frame_start=session.frame_start,
            frame_end=session.frame_end,
            total_work=total_work,
        )
        recovery_frame = session.recovery_frame
        current_frame = session.current_frame if recovery_frame is None else recovery_frame
        if session.is_finished:
            current_frame = session.frame_end
            status = "No pending frames; finalizing..."
        elif recovery_frame is not None:
            status = f"Restoring resume history at frame {recovery_frame}"
        else:
            status = f"Processing frame {current_frame} of {session.frame_end}"
        self._lifecycle.update(
            phase=OperationPhase.PROCESSING,
            current_frame=current_frame,
            completed_work=len(session.retained_frames),
            status=status,
        )
        self._lifecycle.enter_modal()

    def handle_event(self, event: Any) -> set[Any]:
        """Handle one Blender event without performing more than one bounded session step."""
        if event.type == "ESC":
            self.request_cancel()
            return {"RUNNING_MODAL"}
        if event.type != "TIMER" or not self._lifecycle.accepts_timer_event(event):
            return {"PASS_THROUGH"}
        return self._handle_timer()

    @property
    def cancel_requested(self) -> bool:
        """Whether either the controller or its scene-visible runtime has a pending request."""
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

    def cancel(self) -> None:
        """Release resources when Blender cancels the operator externally."""
        message = "Cancelled by Blender"
        if not self.finalize(OperationPhase.CANCELLED, message):
            self._operator.report({"ERROR"}, self._visible_status(message))

    def fail_initialization(self, frame_number: int, error: Exception) -> None:
        """Publish an initialization error through the same lifecycle finalizer."""
        message = f"Processing failed during initialization at frame {frame_number}: {error}"
        self.finalize(OperationPhase.FAILED, message)
        self._operator.report({"ERROR"}, self._visible_status(message))

    def finalize(self, phase: OperationPhase, status: str) -> bool:
        """Finalize idempotently and return whether cleanup reached the requested outcome."""
        cleanup_succeeded = True
        self._finalized = True
        try:
            self._lifecycle.finalize(phase, status)
        except Exception:
            # The lifecycle publishes the cleanup failure before re-raising; keep Blender's
            # callback boundary intact while preserving the failed outcome for the caller.
            cleanup_succeeded = False
        self._set_status(self._visible_status(status))
        return cleanup_succeeded

    def _handle_timer(self) -> set[Any]:
        session = self._session
        if session is None:
            return self._fail_step(
                0,
                RuntimeError("the incremental session is unavailable"),
            )

        if self.cancel_requested:
            message = f"Cancelled after {len(session.completed_frames)} frame(s)"
            cleanup_succeeded = self.finalize(OperationPhase.CANCELLED, message)
            report_level = {"WARNING"} if cleanup_succeeded else {"ERROR"}
            self._operator.report(report_level, self._visible_status(message))
            return {"CANCELLED"}

        recovering_history = session.recovery_frame is not None
        frame_number = (
            session.current_frame if session.recovery_frame is None else session.recovery_frame
        )
        completed_before = len(session.completed_frames)
        retained_before = len(session.retained_frames)
        try:
            session.process_next_frame()
            completed = len(session.completed_frames)
            if completed > completed_before:
                self._publish_step(
                    frame_number,
                    f"Processed frame {frame_number} of {session.frame_end}",
                )
            elif recovering_history:
                status = (
                    f"Reset invalid resume history at frame {frame_number}"
                    if len(session.retained_frames) < retained_before
                    else f"Restored resume history through frame {frame_number}"
                )
                self._publish_step(frame_number, status)
        except SequenceProcessingCancelled as error:
            message = f"Cancelled after {len(error.completed_frames)} frame(s)"
            cleanup_succeeded = self.finalize(OperationPhase.CANCELLED, message)
            report_level = {"WARNING"} if cleanup_succeeded else {"ERROR"}
            self._operator.report(report_level, self._visible_status(message))
            return {"CANCELLED"}
        except Exception as error:
            return self._fail_step(frame_number, error)

        if not session.is_finished:
            return {"RUNNING_MODAL"}
        try:
            result = session.result
        except Exception as error:
            return self._fail_step(frame_number, error)
        message = f"Processed {len(result.frames)} frame(s)"
        if not self.finalize(OperationPhase.COMPLETED, message):
            self._operator.report({"ERROR"}, self._visible_status(message))
            return {"CANCELLED"}
        self._operator.report({"INFO"}, message)
        return {"FINISHED"}

    def _publish_step(self, frame_number: int, status: str) -> None:
        session = self._session
        if session is None:
            raise RuntimeError("the incremental session is unavailable")
        self._lifecycle.update(
            phase=OperationPhase.PROCESSING,
            current_frame=frame_number,
            completed_work=len(session.retained_frames),
            status=status,
        )

    def _fail_step(self, frame_number: int, error: Exception) -> set[Any]:
        session = self._session
        message = f"Processing failed during processing at frame {frame_number}: {error}"
        completed_work = 0
        if session is not None:
            completed_work = len(session.retained_frames)
        with suppress(Exception):
            self._lifecycle.update(
                phase=OperationPhase.FAILED,
                current_frame=frame_number,
                completed_work=completed_work,
                status=message,
            )
        self.finalize(OperationPhase.FAILED, message)
        self._operator.report({"ERROR"}, self._visible_status(message))
        return {"CANCELLED"}

    def _set_status(self, status: str) -> None:
        with suppress(Exception):
            self._settings.status = status

    def _visible_status(self, fallback: str) -> str:
        try:
            return self._runtime.status
        except Exception:
            return fallback

    def _cleanup_session(self) -> None:
        self._session = None
        if self._on_cleanup is not None:
            self._on_cleanup()
