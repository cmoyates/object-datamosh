from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from object_datamosh.modal_lifecycle import ModalOperationLifecycle, OperationPhase


@dataclass
class RuntimeState:
    active: bool = False
    cancel_requested: bool = True
    phase: str = OperationPhase.IDLE.value
    run_identity: str = "stale"
    current_frame: int = 0
    frame_start: int = 0
    frame_end: int = 0
    completed_work: int = 0
    total_work: int = 0
    progress: float = 0.0
    status: str = "stale"


class Area:
    type = "VIEW_3D"

    def __init__(self) -> None:
        self.redraws = 0

    def tag_redraw(self) -> None:
        self.redraws += 1


class WindowManager:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.timer = object()
        self.windows = [type("Window", (), {"screen": type("Screen", (), {"areas": [Area()]})()})()]

    def event_timer_add(self, interval: float, *, window: object) -> object:
        self.events.append(("timer_add", (interval, window)))
        return self.timer

    def event_timer_remove(self, timer: object) -> None:
        self.events.append(("timer_remove", timer))

    def modal_handler_add(self, operator: object) -> None:
        self.events.append(("modal_handler_add", operator))

    def progress_begin(self, minimum: int, maximum: int) -> None:
        self.events.append(("progress_begin", (minimum, maximum)))

    def progress_update(self, value: int) -> None:
        self.events.append(("progress_update", value))

    def progress_end(self) -> None:
        self.events.append(("progress_end", None))


@dataclass
class Context:
    window_manager: WindowManager
    window: object


def test_begin_exposes_a_fresh_scene_run_and_installs_one_modal_timer() -> None:
    runtime = RuntimeState()
    window_manager = WindowManager()
    window = object()
    context = Context(window_manager, window)
    operator = object()
    lifecycle = ModalOperationLifecycle(
        operator,
        runtime,
        run_identity_factory=lambda: "run-22",
    )

    lifecycle.begin(context, frame_start=3, frame_end=5, total_work=3)
    lifecycle.enter_modal()

    assert runtime == RuntimeState(
        active=True,
        cancel_requested=False,
        phase="INITIALIZING",
        run_identity="run-22",
        current_frame=3,
        frame_start=3,
        frame_end=5,
        completed_work=0,
        total_work=3,
        progress=0.0,
        status="Initializing...",
    )
    assert window_manager.events == [
        ("progress_begin", (0, 3)),
        ("timer_add", (0.1, window)),
        ("modal_handler_add", operator),
    ]


def test_timer_events_without_identity_follow_the_owned_timer_cadence() -> None:
    now = 10.0

    def clock() -> float:
        return now

    runtime = RuntimeState()
    window_manager = WindowManager()
    lifecycle = ModalOperationLifecycle(object(), runtime, timer_interval=0.1, clock=clock)
    lifecycle.begin(Context(window_manager, object()), frame_start=1, frame_end=1, total_work=1)
    unidentified_timer = type("TimerEvent", (), {})()

    assert not lifecycle.accepts_timer_event(unidentified_timer)
    now = 10.1
    assert lifecycle.accepts_timer_event(unidentified_timer)
    assert not lifecycle.accepts_timer_event(unidentified_timer)
    assert lifecycle.accepts_timer_event(
        type("OwnedTimerEvent", (), {"timer": window_manager.timer})()
    )
    assert not lifecycle.accepts_timer_event(
        type("ForeignTimerEvent", (), {"timer": object()})()
    )


def test_unused_lifecycle_cannot_update_or_cancel_another_run() -> None:
    runtime = RuntimeState(
        active=True, cancel_requested=False, run_identity="another-run"
    )
    lifecycle = ModalOperationLifecycle(object(), runtime)

    try:
        lifecycle.update(
            phase=OperationPhase.PROCESSING,
            current_frame=1,
            completed_work=0,
            status="must not be published",
        )
    except RuntimeError as error:
        assert str(error) == "The modal lifecycle has not begun"
    else:
        raise AssertionError("an unused lifecycle updated another run")

    assert not lifecycle.request_cancel()
    assert runtime.active
    assert not runtime.cancel_requested
    assert runtime.run_identity == "another-run"
    assert runtime.phase == "IDLE"
    assert runtime.status == "stale"


def test_update_publishes_bounded_progress_and_redraws_the_sidebar() -> None:
    runtime = RuntimeState()
    window_manager = WindowManager()
    context = Context(window_manager, object())
    lifecycle = ModalOperationLifecycle(object(), runtime)
    lifecycle.begin(context, frame_start=3, frame_end=5, total_work=3)

    lifecycle.update(
        phase=OperationPhase.PROCESSING,
        current_frame=4,
        completed_work=2,
        status="Processing frame 4 of 5",
    )

    assert runtime.phase == "PROCESSING"
    assert runtime.current_frame == 4
    assert runtime.completed_work == 2
    assert runtime.progress == 2 / 3
    assert runtime.status == "Processing frame 4 of 5"
    assert window_manager.events[-1] == ("progress_update", 2)
    assert window_manager.windows[0].screen.areas[0].redraws == 1


def test_request_cancel_acknowledges_pending_cancellation_without_finalizing() -> None:
    runtime = RuntimeState()
    window_manager = WindowManager()
    lifecycle = ModalOperationLifecycle(object(), runtime)
    lifecycle.begin(Context(window_manager, object()), frame_start=1, frame_end=2, total_work=2)

    assert lifecycle.request_cancel()

    assert runtime.active
    assert runtime.cancel_requested
    assert runtime.phase == "CANCELLING"
    assert runtime.status == "Cancel requested; waiting for a safe boundary..."
    assert not lifecycle.request_cancel()
    assert all(event[0] != "timer_remove" for event in window_manager.events)


def test_finalize_runs_workflow_cleanup_once_and_releases_all_universal_state() -> None:
    runtime = RuntimeState()
    window_manager = WindowManager()
    cleanup_calls: list[str] = []
    lifecycle = ModalOperationLifecycle(
        object(),
        runtime,
        cleanup=lambda: cleanup_calls.append("cleanup"),
    )
    lifecycle.begin(Context(window_manager, object()), frame_start=1, frame_end=2, total_work=2)

    lifecycle.finalize(OperationPhase.COMPLETED, "Complete")
    lifecycle.finalize(OperationPhase.FAILED, "must be ignored")

    assert cleanup_calls == ["cleanup"]
    assert not runtime.active
    assert not runtime.cancel_requested
    assert runtime.phase == "COMPLETED"
    assert runtime.status == "Complete"
    assert window_manager.events[-2:] == [
        ("timer_remove", window_manager.timer),
        ("progress_end", None),
    ]


def test_progress_initialization_failure_attempts_matching_cleanup() -> None:
    class FailingProgressWindowManager(WindowManager):
        def progress_begin(self, minimum: int, maximum: int) -> None:
            super().progress_begin(minimum, maximum)
            raise RuntimeError("progress unavailable")

    runtime = RuntimeState()
    window_manager = FailingProgressWindowManager()
    lifecycle = ModalOperationLifecycle(object(), runtime)

    try:
        lifecycle.begin(
            Context(window_manager, object()), frame_start=1, frame_end=1, total_work=1
        )
    except RuntimeError as error:
        assert str(error) == "progress unavailable"
    else:
        raise AssertionError("progress initialization failure did not propagate")

    assert not runtime.active
    assert runtime.phase == "FAILED"
    assert window_manager.events == [
        ("progress_begin", (0, 1)),
        ("progress_end", None),
    ]


def test_partial_initialization_failure_cleans_up_and_unlocks_the_runtime() -> None:
    class FailingWindowManager(WindowManager):
        def event_timer_add(self, interval: float, *, window: object) -> object:
            del interval, window
            raise RuntimeError("timer unavailable")

    runtime = RuntimeState()
    window_manager = FailingWindowManager()
    lifecycle = ModalOperationLifecycle(object(), runtime)

    try:
        lifecycle.begin(
            Context(window_manager, object()), frame_start=1, frame_end=1, total_work=1
        )
    except RuntimeError as error:
        assert str(error) == "timer unavailable"
    else:
        raise AssertionError("initialization failure did not propagate")

    assert not runtime.active
    assert runtime.phase == "FAILED"
    assert runtime.status == "Initialization failed at frame 1: timer unavailable"
    assert window_manager.events == [
        ("progress_begin", (0, 1)),
        ("progress_end", None),
    ]


def test_run_identity_failure_does_not_lock_the_runtime() -> None:
    runtime = RuntimeState()

    def fail_identity() -> str:
        raise RuntimeError("identity unavailable")

    lifecycle = ModalOperationLifecycle(
        object(), runtime, run_identity_factory=fail_identity
    )

    try:
        lifecycle.begin(
            Context(WindowManager(), object()), frame_start=1, frame_end=1, total_work=1
        )
    except RuntimeError as error:
        assert str(error) == "identity unavailable"
    else:
        raise AssertionError("run identity failure did not propagate")

    assert not runtime.active
    assert runtime.run_identity == "stale"


def test_active_scene_state_rejects_a_second_lifecycle() -> None:
    runtime = RuntimeState(active=True, run_identity="first-run")
    lifecycle = ModalOperationLifecycle(object(), runtime)

    try:
        lifecycle.begin(
            Context(WindowManager(), object()), frame_start=1, frame_end=1, total_work=1
        )
    except RuntimeError as error:
        assert str(error) == "Another Object Datamosh operation is already active"
    else:
        raise AssertionError("a second lifecycle acquired active scene state")

    assert runtime.active
    assert runtime.run_identity == "first-run"


def test_finalizing_an_unused_lifecycle_does_not_release_another_run() -> None:
    runtime = RuntimeState(active=True, run_identity="another-run")
    lifecycle = ModalOperationLifecycle(object(), runtime)

    lifecycle.finalize(OperationPhase.FAILED, "ignored")

    assert runtime.active
    assert runtime.run_identity == "another-run"
    assert runtime.phase == "IDLE"
    assert runtime.status == "stale"


def test_finalize_is_safe_before_timer_creation() -> None:
    runtime = RuntimeState()
    cleanup_calls: list[str] = []
    lifecycle = ModalOperationLifecycle(
        object(), runtime, cleanup=lambda: cleanup_calls.append("cleanup")
    )

    lifecycle.finalize(OperationPhase.FAILED, "Could not initialize")
    lifecycle.finalize(OperationPhase.COMPLETED, "ignored")

    assert cleanup_calls == ["cleanup"]
    assert not runtime.active
    assert runtime.phase == "FAILED"
    assert runtime.status == "Could not initialize"


def test_timer_removal_failure_still_ends_progress_and_unlocks_runtime() -> None:
    class FailingRemovalWindowManager(WindowManager):
        def event_timer_remove(self, timer: object) -> None:
            super().event_timer_remove(timer)
            raise ValueError("timer already removed")

    runtime = RuntimeState()
    window_manager = FailingRemovalWindowManager()
    lifecycle = ModalOperationLifecycle(object(), runtime)
    lifecycle.begin(Context(window_manager, object()), frame_start=1, frame_end=1, total_work=1)

    try:
        lifecycle.finalize(OperationPhase.COMPLETED, "Complete")
    except ValueError as error:
        assert str(error) == "timer already removed"
    else:
        raise AssertionError("timer cleanup failure did not propagate")

    assert not runtime.active
    assert runtime.phase == "FAILED"
    assert runtime.status == "Complete; cleanup failed: timer already removed"
    assert window_manager.events[-1] == ("progress_end", None)


def test_cleanup_failure_still_releases_universal_resources_and_reports_failure() -> None:
    def fail_cleanup() -> None:
        raise RuntimeError("session cleanup failed")

    runtime = RuntimeState()
    window_manager = WindowManager()
    lifecycle = ModalOperationLifecycle(object(), runtime, cleanup=fail_cleanup)
    lifecycle.begin(Context(window_manager, object()), frame_start=1, frame_end=1, total_work=1)

    try:
        lifecycle.finalize(OperationPhase.COMPLETED, "Complete")
    except RuntimeError as error:
        assert str(error) == "session cleanup failed"
    else:
        raise AssertionError("workflow cleanup failure did not propagate")

    assert not runtime.active
    assert runtime.phase == "FAILED"
    assert runtime.status == "Complete; cleanup failed: session cleanup failed"
    assert window_manager.events[-2:] == [
        ("timer_remove", window_manager.timer),
        ("progress_end", None),
    ]
