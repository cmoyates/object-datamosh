"""State and modal coordination for the combined render-and-process workflow."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from .core.paths import FramePaths
from .modal_lifecycle import ModalOperationLifecycle, OperationPhase, RuntimeState
from .raw_render_operation import (
    RawRenderModalController,
    RenderAdapter,
    RenderSession,
    ReportingOperator,
    StatusSettings,
)
from .sequence_processing import SequenceProcessingCancelled


class RenderAndProcessState(StrEnum):
    """Explicit states of one combined workflow."""

    INITIALIZING = "INITIALIZING"
    RENDERING = "RENDERING"
    PROCESSING = "PROCESSING"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


@dataclass(slots=True)
class RenderAndProcessStateMachine:
    """Pure progress state for one frame-bounded combined operation."""

    frame_start: int
    frame_end: int
    state: RenderAndProcessState = RenderAndProcessState.INITIALIZING
    rendered_count: int = 0
    processed_count: int = 0

    def __post_init__(self) -> None:
        if self.frame_start > self.frame_end:
            raise ValueError("frame_start must not be greater than frame_end")

    @property
    def frame_count(self) -> int:
        return self.frame_end - self.frame_start + 1

    @property
    def current_frame(self) -> int:
        if self.state is RenderAndProcessState.RENDERING:
            return min(self.frame_start + self.rendered_count, self.frame_end)
        if self.state is RenderAndProcessState.PROCESSING:
            return min(self.frame_start + self.processed_count, self.frame_end)
        return self.frame_start

    def begin_rendering(self) -> None:
        """Enter rendering from initialization."""
        if self.state is not RenderAndProcessState.INITIALIZING:
            raise RuntimeError("Rendering can only begin during initialization")
        self.state = RenderAndProcessState.RENDERING

    def record_rendered_frame(self, frame_number: int) -> None:
        """Record the next successfully discovered raw frame."""
        if self.is_finalizing or self.is_terminal:
            return
        if self.state is not RenderAndProcessState.RENDERING:
            raise RuntimeError("A rendered frame can only advance rendering")
        if self.rendered_count >= self.frame_count:
            raise RuntimeError("Rendering has no remaining frames to record")
        expected = self.frame_start + self.rendered_count
        if frame_number != expected:
            raise ValueError(f"Expected rendered frame {expected}, got {frame_number}")
        self.rendered_count += 1

    def begin_processing(self) -> None:
        """Enter processing only after every raw frame has been discovered."""
        if self.state is not RenderAndProcessState.RENDERING:
            raise RuntimeError("Processing can only begin after rendering")
        if self.rendered_count != self.frame_count:
            raise RuntimeError("Processing cannot begin before rendering is complete")
        self.state = RenderAndProcessState.PROCESSING

    def record_processed_frame(self, frame_number: int) -> None:
        """Record the next completed processing frame."""
        if self.is_finalizing or self.is_terminal:
            return
        if self.state is not RenderAndProcessState.PROCESSING:
            raise RuntimeError("A processed frame can only advance processing")
        if self.processed_count >= self.frame_count:
            raise RuntimeError("Processing has no remaining frames to record")
        expected = self.frame_start + self.processed_count
        if frame_number != expected:
            raise ValueError(f"Expected processed frame {expected}, got {frame_number}")
        self.processed_count += 1

    def complete(self) -> None:
        """Finalize a workflow whose two phases completed their full ranges."""
        if (
            self.state is not RenderAndProcessState.PROCESSING
            or self.processed_count != self.frame_count
        ):
            raise RuntimeError("The workflow cannot complete while work remains")
        self.state = RenderAndProcessState.FINALIZING

    def cancel(self) -> None:
        """Finalize an active workflow as cancelled at its current safe boundary."""
        if self.is_finalizing or self.is_terminal:
            return
        self.state = RenderAndProcessState.FINALIZING

    def fail(self) -> None:
        """Finalize an active workflow as failed."""
        if self.is_finalizing or self.is_terminal:
            return
        self.state = RenderAndProcessState.FINALIZING

    def finish(self, terminal_state: RenderAndProcessState) -> None:
        """Commit a terminal state after shared lifecycle cleanup has run."""
        if self.is_terminal:
            return
        if self.state is not RenderAndProcessState.FINALIZING:
            raise RuntimeError("The workflow must enter finalization before it can finish")
        if terminal_state not in {
            RenderAndProcessState.COMPLETED,
            RenderAndProcessState.CANCELLED,
            RenderAndProcessState.FAILED,
        }:
            raise ValueError("finish requires a terminal workflow state")
        self.state = terminal_state

    @property
    def is_finalizing(self) -> bool:
        return self.state is RenderAndProcessState.FINALIZING

    @property
    def is_terminal(self) -> bool:
        return self.state in {
            RenderAndProcessState.COMPLETED,
            RenderAndProcessState.CANCELLED,
            RenderAndProcessState.FAILED,
        }

    @property
    def completed_work(self) -> int:
        return self.rendered_count + self.processed_count

    @property
    def total_work(self) -> int:
        return self.frame_count * 2

    @property
    def progress(self) -> float:
        return self.completed_work / self.total_work


class ProcessingResult(Protocol):
    """Successful output boundary required from incremental processing."""

    @property
    def frames(self) -> tuple[Path, ...]: ...


class IncrementalProcessingSession(Protocol):
    """Processing session surface coordinated by the combined workflow."""

    current_frame: int
    completed_frames: tuple[Path, ...]

    @property
    def is_finished(self) -> bool: ...

    @property
    def result(self) -> ProcessingResult: ...

    def process_next_frame(self) -> None: ...


ProcessingFactory = Callable[
    [tuple[FramePaths, ...], Callable[[], bool]], IncrementalProcessingSession
]


class RenderAndProcessModalController:
    """Coordinate rendering and processing through one modal lifecycle."""

    def __init__(
        self,
        operator: ReportingOperator,
        runtime: RuntimeState,
        settings: StatusSettings,
        *,
        adapter: RenderAdapter,
        create_processing: ProcessingFactory,
        on_cleanup: Callable[[], None] | None = None,
        run_identity_factory: Callable[[], str] | None = None,
    ) -> None:
        self._operator = operator
        self._runtime = runtime
        self._settings = settings
        self._adapter = adapter
        self._create_processing = create_processing
        self._on_cleanup = on_cleanup
        self._render_session: RenderSession | None = None
        self._processing_session: IncrementalProcessingSession | None = None
        self._state: RenderAndProcessStateMachine | None = None
        self._cancel_requested = False
        self._lifecycle = ModalOperationLifecycle(
            operator,
            runtime,
            cleanup=self._cleanup,
            run_identity_factory=run_identity_factory,
        )
        self._render_controller = RawRenderModalController(
            operator,
            runtime,
            settings,
            adapter=adapter,
            lifecycle=self._lifecycle,
            on_complete=self._begin_processing,
            on_frame_completed=self._record_rendered_frame,
            on_cancelled=lambda _completed: self._finish_cancelled(),
            on_failed=self._fail_rendering,
        )

    def start(self, context: Any, render_session: RenderSession) -> None:
        """Install the sole modal lifecycle and enter the rendering phase."""
        self._render_session = render_session
        self._render_controller.attach(render_session)
        state = RenderAndProcessStateMachine(
            frame_start=render_session.frame_start,
            frame_end=render_session.frame_end,
        )
        self._state = state
        self._lifecycle.begin(
            context,
            frame_start=state.frame_start,
            frame_end=state.frame_end,
            total_work=state.total_work,
            phase_total_work=state.frame_count,
        )
        state.begin_rendering()
        self._lifecycle.update(
            phase=OperationPhase.RENDERING,
            current_frame=state.current_frame,
            completed_work=state.completed_work,
            status=f"Ready to render frame {state.current_frame} of {state.frame_end}",
        )
        self._lifecycle.enter_modal()

    def handle_event(self, event: Any) -> set[Any]:
        """Advance at most one render observation or processing frame."""
        state = self._state
        if state is None or state.is_terminal:
            return {"CANCELLED"}
        if state.state is RenderAndProcessState.RENDERING:
            return self._render_controller.handle_event(event)
        if event.type == "ESC":
            self.request_cancel()
            return {"RUNNING_MODAL"}
        if event.type != "TIMER" or not self._lifecycle.accepts_timer_event(event):
            return {"PASS_THROUGH"}
        return self._advance_processing()

    def request_cancel(self) -> bool:
        """Publish cancellation for whichever phase currently owns the safe boundary."""
        state = self._state
        if state is None or state.is_terminal:
            return False
        if self.cancel_requested:
            return True
        self._cancel_requested = True
        if state.state is RenderAndProcessState.RENDERING:
            self._render_controller.request_cancel()
        else:
            with suppress(Exception):
                self._lifecycle.request_cancel()
        return True

    @property
    def cancel_requested(self) -> bool:
        """Whether cancellation is pending even if scene-owned RNA is unavailable."""
        if self._cancel_requested:
            return True
        try:
            return self._runtime.cancel_requested
        except Exception:
            return False

    def cancel(self) -> None:
        """Finalize safely when Blender cancels the owning operator."""
        state = self._state
        if state is None or state.is_terminal:
            return
        self._finish_cancelled()

    def fail_initialization(self, frame_number: int, error: Exception) -> None:
        """Route partial setup failure through the combined finalizer."""
        self._fail("initialization", frame_number, error)

    def _record_rendered_frame(self, completed_frame: FramePaths) -> None:
        self._require_state().record_rendered_frame(completed_frame.frame)

    def _begin_processing(self, completed_session: RenderSession) -> set[Any]:
        state = self._require_state()
        try:
            session = self._render_session
            if session is None or completed_session is not session:
                raise RuntimeError("raw rendering returned an unexpected session")
            frames = session.completed_frames
            if len(frames) != state.rendered_count:
                raise RuntimeError("raw rendering completed with inconsistent discovered outputs")
            processing = self._create_processing(frames, lambda: self.cancel_requested)
            self._processing_session = processing
            state.begin_processing()
            current_frame = processing.current_frame
            configuration_name = getattr(processing, "configuration_name", "Unknown configuration")
            self._lifecycle.update(
                phase=OperationPhase.PROCESSING,
                current_frame=current_frame,
                completed_work=state.completed_work,
                status=(
                    f"Processing: {configuration_name} (frame {current_frame} of {state.frame_end})"
                ),
                phase_completed_work=state.processed_count,
                phase_total_work=state.frame_count,
            )
        except Exception as error:
            return self._fail("transition to processing", state.frame_start, error)
        return {"RUNNING_MODAL"}

    def _advance_processing(self) -> set[Any]:
        state = self._require_state()
        session = self._processing_session
        if session is None:
            return self._fail(
                "processing", state.current_frame, RuntimeError("processing session unavailable")
            )
        if self.cancel_requested:
            return self._finish_cancelled()
        frame_number = session.current_frame
        completed_before = len(session.completed_frames)
        completed_frame = False
        try:
            session.process_next_frame()
            completed_frame = len(session.completed_frames) > completed_before
            if completed_frame:
                state.record_processed_frame(frame_number)
        except SequenceProcessingCancelled:
            return self._finish_cancelled()
        except Exception as error:
            return self._fail("processing", frame_number, error)
        if completed_frame:
            try:
                self._lifecycle.update(
                    phase=OperationPhase.PROCESSING,
                    current_frame=frame_number,
                    completed_work=state.completed_work,
                    status=(
                        "Processing: "
                        f"{getattr(session, 'configuration_name', 'Unknown configuration')} "
                        f"(processed frame {frame_number} of {state.frame_end})"
                    ),
                    phase_completed_work=state.processed_count,
                    phase_total_work=state.frame_count,
                )
            except Exception as error:
                return self._fail("processing", frame_number, error)
        if not session.is_finished:
            return {"RUNNING_MODAL"}
        try:
            result = session.result
            state.complete()
        except Exception as error:
            return self._fail("processing", frame_number, error)
        configuration_name = getattr(session, "configuration_name", "Unknown configuration")
        report_path = getattr(
            session,
            "report_path",
            getattr(session, "manifest_path", "processing report unavailable"),
        )
        message = (
            f"Render and Process complete: {len(result.frames)} frame(s) with "
            f"{configuration_name}; report: {report_path}"
        )
        warnings = tuple(getattr(session, "advisory_warnings", ()))
        if warnings:
            message += "; warning: " + " | ".join(warnings)
        return self._finalize(
            OperationPhase.COMPLETED,
            message,
            {"FINISHED"},
            {"WARNING"} if warnings else {"INFO"},
        )

    def _finish_cancelled(self) -> set[Any]:
        state = self._require_state()
        report_error = self._write_processing_terminal_report("CANCELLED")
        state.cancel()
        message = (
            f"Render and Process cancelled after {state.completed_work} of {state.total_work} steps"
        )
        if report_error is not None:
            message += f"; diagnostics report write failed: {report_error}"
        return self._finalize(OperationPhase.CANCELLED, message, {"CANCELLED"}, {"WARNING"})

    def _fail_rendering(self, frame_number: int, error: Exception) -> set[Any]:
        return self._fail("rendering", frame_number, error)

    def _fail(self, phase: str, frame_number: int, error: Exception) -> set[Any]:
        report_error = self._write_processing_terminal_report("FAILURE", failure=str(error))
        if self._state is not None:
            self._state.fail()
        message = f"Render and Process failed during {phase} at frame {frame_number}: {error}"
        if report_error is not None:
            message += f"; diagnostics report write failed: {report_error}"
        return self._finalize(OperationPhase.FAILED, message, {"CANCELLED"}, {"ERROR"})

    def _write_processing_terminal_report(
        self, outcome: Literal["CANCELLED", "FAILURE"], *, failure: str | None = None
    ) -> Exception | None:
        """Write processing diagnostics and preserve failures for the visible terminal status."""
        if self._processing_session is None:
            return None
        writer = getattr(self._processing_session, "write_terminal_report", None)
        if not callable(writer):
            return None
        try:
            writer(outcome, failure=failure)
        except Exception as error:
            return error
        return None

    def _finalize(
        self,
        phase: OperationPhase,
        message: str,
        result: set[Any],
        report_level: set[str],
    ) -> set[Any]:
        terminal_state = {
            OperationPhase.COMPLETED: RenderAndProcessState.COMPLETED,
            OperationPhase.CANCELLED: RenderAndProcessState.CANCELLED,
            OperationPhase.FAILED: RenderAndProcessState.FAILED,
        }[phase]
        try:
            self._lifecycle.finalize(phase, message)
        except Exception:
            report_level = {"ERROR"}
            result = {"CANCELLED"}
            terminal_state = RenderAndProcessState.FAILED
        state = self._state
        if state is not None:
            state.finish(terminal_state)
        try:
            visible = self._runtime.status
        except Exception:
            visible = message
        with suppress(Exception):
            self._settings.status = visible
        self._operator.report(report_level, visible)
        return result

    def _require_state(self) -> RenderAndProcessStateMachine:
        if self._state is None:
            raise RuntimeError("the combined workflow is unavailable")
        return self._state

    def _cleanup(self) -> None:
        cleanup_errors: list[Exception] = []
        try:
            self._render_controller.release_resources()
        except Exception as error:
            cleanup_errors.append(error)
        self._render_session = None
        self._processing_session = None
        if self._on_cleanup is not None:
            try:
                self._on_cleanup()
            except Exception as error:
                cleanup_errors.append(error)
        if len(cleanup_errors) == 1:
            raise cleanup_errors[0]
        if cleanup_errors:
            raise RuntimeError("; ".join(str(error) for error in cleanup_errors))
