"""Modal controller for processing existing pass sequences.

This module owns the operation state machine without defining Blender RNA types. The UI operator
constructs the processing session and delegates event handling here.
"""

from __future__ import annotations

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
    ) -> None:
        self._operator = operator
        self._runtime = runtime
        self._settings = settings
        self._session: ProcessingSession | None = None
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
        status = (
            f"Restoring resume history at frame {recovery_frame}"
            if recovery_frame is not None
            else f"Processing frame {current_frame} of {session.frame_end}"
        )
        self._settings.status = status
        self._lifecycle.update(
            phase=OperationPhase.PROCESSING,
            current_frame=current_frame,
            completed_work=len(session.retained_frames),
            status=status,
        )

    def handle_event(self, event: Any) -> set[Any]:
        """Handle one Blender event without performing more than one bounded session step."""
        if event.type == "ESC":
            self._lifecycle.request_cancel()
            return {"RUNNING_MODAL"}
        if event.type != "TIMER" or not self._lifecycle.owns_timer_event(event):
            return {"PASS_THROUGH"}
        return self._handle_timer()

    def cancel(self) -> None:
        """Release resources when Blender cancels the operator externally."""
        message = "Cancelled by Blender"
        self._settings.status = message
        self.finalize(OperationPhase.CANCELLED, message)

    def fail_initialization(self, frame_number: int, error: Exception) -> None:
        """Publish an initialization error through the same lifecycle finalizer."""
        message = f"Processing failed during initialization at frame {frame_number}: {error}"
        self._settings.status = message
        self.finalize(OperationPhase.FAILED, message)
        self._operator.report({"ERROR"}, message)

    def finalize(self, phase: OperationPhase, status: str) -> None:
        """Finalize idempotently and synchronize the canonical terminal status."""
        # The lifecycle publishes a cleanup failure before re-raising. A Blender callback must
        # still return control rather than strand the modal operator in an exception.
        with suppress(Exception):
            self._lifecycle.finalize(phase, status)
        self._settings.status = self._runtime.status

    def _handle_timer(self) -> set[Any]:
        session = self._session
        if session is None:
            return self._fail_step(
                0,
                RuntimeError("the incremental session is unavailable"),
            )

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
            self._settings.status = message
            self.finalize(OperationPhase.CANCELLED, message)
            self._operator.report({"WARNING"}, message)
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
        self._settings.status = message
        self.finalize(OperationPhase.COMPLETED, message)
        self._operator.report({"INFO"}, message)
        return {"FINISHED"}

    def _publish_step(self, frame_number: int, status: str) -> None:
        session = self._session
        if session is None:
            raise RuntimeError("the incremental session is unavailable")
        self._settings.status = status
        self._lifecycle.update(
            phase=OperationPhase.PROCESSING,
            current_frame=frame_number,
            completed_work=len(session.retained_frames),
            status=status,
        )

    def _fail_step(self, frame_number: int, error: Exception) -> set[Any]:
        session = self._session
        message = f"Processing failed during processing at frame {frame_number}: {error}"
        self._settings.status = message
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
        self._operator.report({"ERROR"}, message)
        return {"CANCELLED"}

    def _cleanup_session(self) -> None:
        self._session = None
