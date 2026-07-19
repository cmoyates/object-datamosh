"""Reusable lifecycle for Object Datamosh modal Blender operations.

The lifecycle owns only universal modal concerns. Workflow-specific resources remain behind the
cleanup hook supplied by the operator that created the lifecycle.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from enum import StrEnum
from time import monotonic
from typing import Any, Protocol
from uuid import uuid4


class OperationPhase(StrEnum):
    """Stable scene-visible phases shared by current and combined workflows."""

    IDLE = "IDLE"
    INITIALIZING = "INITIALIZING"
    RENDERING = "RENDERING"
    PROCESSING = "PROCESSING"
    FINALIZING = "FINALIZING"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"


class RuntimeState(Protocol):
    """Writable scene property surface required by the lifecycle."""

    active: bool
    cancel_requested: bool
    phase: str
    run_identity: str
    current_frame: int
    frame_start: int
    frame_end: int
    completed_work: int
    total_work: int
    phase_completed_work: int
    phase_total_work: int
    progress: float
    status: str


class ModalOperationLifecycle:
    """Own one Blender modal timer, progress display, runtime state, and finalization."""

    def __init__(
        self,
        operator: object,
        runtime: RuntimeState,
        *,
        cleanup: Callable[[], None] | None = None,
        timer_interval: float = 0.1,
        run_identity_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._operator = operator
        self._runtime = runtime
        self._cleanup = cleanup
        self._timer_interval = timer_interval
        self._run_identity_factory = run_identity_factory or (lambda: uuid4().hex)
        self._clock = clock
        self._timer: object | None = None
        self._next_step_deadline: float | None = None
        self._window_manager: Any | None = None
        self._progress_started = False
        self._handler_added = False
        self._owns_runtime = False
        self._finalized = False

    def begin(
        self,
        context: Any,
        *,
        frame_start: int,
        frame_end: int,
        total_work: int,
        phase_total_work: int | None = None,
    ) -> None:
        """Expose a fresh run and install its progress display and modal timer."""
        if self._runtime.active:
            raise RuntimeError("Another Object Datamosh operation is already active")
        if total_work < 0:
            raise ValueError("total_work must be non-negative")
        if phase_total_work is None:
            phase_total_work = total_work
        if phase_total_work < 0 or phase_total_work > total_work:
            raise ValueError("phase_total_work must be within the configured total work")

        if self._finalized or self._owns_runtime:
            raise RuntimeError("This modal lifecycle has already been used")

        runtime = self._runtime
        run_identity = self._run_identity_factory()
        try:
            runtime.cancel_requested = False
            runtime.phase = OperationPhase.INITIALIZING.value
            runtime.run_identity = run_identity
            runtime.current_frame = frame_start
            runtime.frame_start = frame_start
            runtime.frame_end = frame_end
            runtime.completed_work = 0
            runtime.total_work = total_work
            runtime.phase_completed_work = 0
            runtime.phase_total_work = phase_total_work
            runtime.progress = 0.0
            runtime.status = "Initializing..."
            # Blender operators run on the main thread, so publishing ``active`` last makes the
            # initialized runtime state the lock acquisition boundary.
            runtime.active = True
            self._owns_runtime = True

            window_manager = context.window_manager
            self._window_manager = window_manager
            # Mark the resource before acquisition so cleanup still attempts ``progress_end`` if
            # Blender raises after partially opening its progress display.
            self._progress_started = True
            window_manager.progress_begin(0, total_work)
            self._timer = window_manager.event_timer_add(
                self._timer_interval,
                window=context.window,
            )
            self._next_step_deadline = self._clock() + self._timer_interval
        except Exception as error:
            with suppress(Exception):
                self.finalize(
                    OperationPhase.FAILED,
                    f"Initialization failed at frame {frame_start}: {error}",
                )
            raise

    def enter_modal(self) -> None:
        """Register the modal handler after every other initialization step has succeeded."""
        if self._finalized or not self._owns_runtime or self._window_manager is None:
            raise RuntimeError("The modal lifecycle is not ready to enter modal handling")
        if self._handler_added:
            raise RuntimeError("The modal handler has already been added")
        self._window_manager.modal_handler_add(self._operator)
        self._handler_added = True

    def update(
        self,
        *,
        phase: OperationPhase,
        current_frame: int,
        completed_work: int,
        status: str,
        phase_completed_work: int | None = None,
        phase_total_work: int | None = None,
    ) -> None:
        """Publish one safe work boundary to Blender's runtime and progress surfaces."""
        if self._finalized:
            return
        if not self._owns_runtime:
            raise RuntimeError("The modal lifecycle has not begun")
        total_work = self._runtime.total_work
        if completed_work < 0 or completed_work > total_work:
            raise ValueError("completed_work must be within the configured total work")
        if phase_total_work is None:
            phase_total_work = self._runtime.phase_total_work
        if phase_completed_work is None:
            phase_completed_work = completed_work
        if phase_total_work < 0 or phase_total_work > total_work:
            raise ValueError("phase_total_work must be within the configured total work")
        if phase_completed_work < 0 or phase_completed_work > phase_total_work:
            raise ValueError("phase_completed_work must be within the current phase total")
        self._runtime.phase = phase.value
        self._runtime.current_frame = current_frame
        self._runtime.completed_work = completed_work
        self._runtime.phase_completed_work = phase_completed_work
        self._runtime.phase_total_work = phase_total_work
        self._runtime.progress = completed_work / total_work if total_work else 0.0
        self._runtime.status = status
        if self._progress_started and self._window_manager is not None:
            self._window_manager.progress_update(completed_work)
        request_sidebar_redraw(self._window_manager)

    def accepts_timer_event(self, event: object) -> bool:
        """Accept only the owned timer, or its cadence when Blender omits timer identity."""
        event_timer = getattr(event, "timer", None)
        if event_timer is not None:
            return event_timer is self._timer
        deadline = self._next_step_deadline
        now = self._clock()
        if deadline is None or now < deadline:
            return False
        # Blender 5.0 does not identify TIMER events, so preserve the owned timer's bounded cadence
        # even when a different add-on emits more frequent timer events.
        self._next_step_deadline = now + self._timer_interval
        return True

    def request_cancel(self) -> bool:
        """Mark active work for cancellation without mutating workflow resources."""
        if self._finalized or not self._owns_runtime:
            return False
        return request_cancellation(self._runtime, self._window_manager)

    def finalize(self, phase: OperationPhase, status: str) -> None:
        """Run workflow and universal cleanup exactly once, including partial initialization."""
        if self._finalized:
            return
        self._finalized = True
        cleanup_errors: list[Exception] = []
        if self._owns_runtime:
            try:
                self._runtime.phase = OperationPhase.FINALIZING.value
                self._runtime.status = "Finalizing..."
            except Exception:
                pass
        try:
            if self._cleanup is not None:
                self._cleanup()
        except Exception as error:
            cleanup_errors.append(error)

        if self._timer is not None and self._window_manager is not None:
            try:
                self._window_manager.event_timer_remove(self._timer)
            except Exception as error:
                cleanup_errors.append(error)
            self._timer = None
            self._next_step_deadline = None
        if self._progress_started and self._window_manager is not None:
            try:
                self._window_manager.progress_end()
            except Exception as error:
                cleanup_errors.append(error)
            self._progress_started = False

        terminal_phase = OperationPhase.FAILED if cleanup_errors else phase
        if cleanup_errors:
            try:
                affected_frame = self._runtime.current_frame
            except Exception:
                affected_frame = "unknown"
            cleanup_detail = "; ".join(str(error) for error in cleanup_errors)
            terminal_status = (
                f"{status}; cleanup failed during finalization at frame {affected_frame}: "
                f"{cleanup_detail}"
            )
        else:
            terminal_status = status
        try:
            runtime_available = self._owns_runtime or not self._runtime.active
        except Exception:
            runtime_available = False
        if runtime_available:
            try:
                if self._owns_runtime:
                    self._runtime.active = False
                    self._runtime.cancel_requested = False
                self._runtime.phase = terminal_phase.value
                self._runtime.status = terminal_status
            except Exception:
                pass
        self._owns_runtime = False
        request_sidebar_redraw(self._window_manager)
        if len(cleanup_errors) == 1:
            raise cleanup_errors[0]
        if cleanup_errors:
            raise RuntimeError("; ".join(str(error) for error in cleanup_errors))


def request_cancellation(runtime: RuntimeState, window_manager: Any | None = None) -> bool:
    """Acknowledge cancellation on scene state; workflow code observes it at a safe boundary."""
    if not runtime.active or runtime.cancel_requested:
        return False
    runtime.cancel_requested = True
    runtime.phase = OperationPhase.CANCELLING.value
    runtime.status = "Cancel requested; waiting for a safe boundary..."
    request_sidebar_redraw(window_manager)
    return True


def request_sidebar_redraw(window_manager: Any | None) -> None:
    """Safely redraw current 3D View sidebars without retaining an initiating area."""
    if window_manager is None:
        return
    try:
        windows = tuple(window_manager.windows)
    except Exception:
        return
    for window in windows:
        try:
            screen = window.screen
            areas = tuple(screen.areas) if screen is not None else ()
        except Exception:
            continue
        for area in areas:
            try:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
            except Exception:
                continue
