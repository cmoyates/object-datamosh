from types import SimpleNamespace
from typing import Any, cast

from object_datamosh.existing_pass_operation import ExistingPassModalController


class WindowManagerRecorder:
    def __init__(
        self,
        *,
        fail_timer_add: bool = False,
        fail_timer_remove: bool = False,
    ) -> None:
        self.timer = object()
        self.windows: tuple[object, ...] = ()
        self.fail_timer_add = fail_timer_add
        self.fail_timer_remove = fail_timer_remove

    def progress_begin(self, _minimum: int, _maximum: int) -> None:
        pass

    def progress_update(self, _value: int) -> None:
        pass

    def progress_end(self) -> None:
        pass

    def event_timer_add(self, _interval: float, *, window: object) -> object:
        del window
        if self.fail_timer_add:
            raise RuntimeError("timer unavailable")
        return self.timer

    def event_timer_remove(self, timer: object) -> None:
        assert timer is self.timer
        if self.fail_timer_remove:
            raise RuntimeError("timer removal failed")

    def modal_handler_add(self, _operator: object) -> None:
        pass


class ReportingOperator:
    def __init__(self) -> None:
        self.reports: list[tuple[set[str], str]] = []

    def report(self, type: set[str], message: str) -> None:
        self.reports.append((type, message))


def test_zero_frame_trail_recovery_is_published_without_falsy_fallback() -> None:
    runtime = SimpleNamespace(
        active=False,
        cancel_requested=False,
        phase="IDLE",
        run_identity="",
        current_frame=0,
        frame_start=0,
        frame_end=0,
        completed_work=0,
        total_work=0,
        progress=0.0,
        status="Ready",
    )
    settings = SimpleNamespace(status="Ready")
    session = SimpleNamespace(
        frame_start=0,
        frame_end=1,
        current_frame=1,
        recovery_frame=0,
        retained_frames=(0,),
        is_finished=False,
    )
    window_manager = WindowManagerRecorder()
    context = SimpleNamespace(window_manager=window_manager, window=object())
    controller = ExistingPassModalController(
        ReportingOperator(),
        cast(Any, runtime),
        cast(Any, settings),
    )

    controller.start(context, cast(Any, session))

    assert runtime.current_frame == 0
    assert runtime.status == "Restoring resume history at frame 0"
    controller.cancel()
    assert not runtime.active


def test_timer_setup_failure_reports_initialization_frame_and_cause() -> None:
    runtime = SimpleNamespace(
        active=False,
        cancel_requested=False,
        phase="IDLE",
        run_identity="",
        current_frame=0,
        frame_start=0,
        frame_end=0,
        completed_work=0,
        total_work=0,
        progress=0.0,
        status="Ready",
    )
    settings = SimpleNamespace(status="Ready")
    session = SimpleNamespace(
        frame_start=7,
        frame_end=7,
        current_frame=7,
        recovery_frame=None,
        retained_frames=(),
        is_finished=False,
    )
    operator = ReportingOperator()
    window_manager = WindowManagerRecorder(fail_timer_add=True)
    controller = ExistingPassModalController(
        operator,
        cast(Any, runtime),
        cast(Any, settings),
    )

    try:
        controller.start(
            SimpleNamespace(window_manager=window_manager, window=object()),
            cast(Any, session),
        )
    except RuntimeError as error:
        controller.fail_initialization(7, error)
    else:
        raise AssertionError("timer setup failure did not propagate")

    expected = "Initialization failed at frame 7: timer unavailable"
    assert runtime.status == expected
    assert operator.reports == [({"ERROR"}, expected)]


def test_completed_resume_publishes_the_configured_end_frame() -> None:
    runtime = SimpleNamespace(
        active=False,
        cancel_requested=False,
        phase="IDLE",
        run_identity="",
        current_frame=0,
        frame_start=0,
        frame_end=0,
        completed_work=0,
        total_work=0,
        progress=0.0,
        status="Ready",
    )
    settings = SimpleNamespace(status="Ready")
    session = SimpleNamespace(
        frame_start=1,
        frame_end=3,
        current_frame=4,
        recovery_frame=None,
        retained_frames=(1, 2, 3),
        completed_frames=(),
        is_finished=True,
    )
    window_manager = WindowManagerRecorder()
    controller = ExistingPassModalController(
        ReportingOperator(),
        cast(Any, runtime),
        cast(Any, settings),
    )

    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        cast(Any, session),
    )

    assert runtime.current_frame == 3
    assert runtime.status == "No pending frames; finalizing..."
    assert controller.handle_event(SimpleNamespace(type="ESC")) == {"RUNNING_MODAL"}
    assert controller.handle_event(
        SimpleNamespace(type="TIMER", timer=window_manager.timer)
    ) == {"CANCELLED"}
    assert runtime.phase == "CANCELLED"


def test_success_is_not_reported_when_lifecycle_cleanup_fails() -> None:
    runtime = SimpleNamespace(
        active=False,
        cancel_requested=False,
        phase="IDLE",
        run_identity="",
        current_frame=0,
        frame_start=0,
        frame_end=0,
        completed_work=0,
        total_work=0,
        progress=0.0,
        status="Ready",
    )
    settings = SimpleNamespace(status="Ready")

    class Session:
        frame_start = 1
        frame_end = 1
        current_frame = 1
        recovery_frame = None
        retained_frames: tuple[int, ...] = ()
        completed_frames: tuple[str, ...] = ()
        is_finished = False

        def process_next_frame(self) -> None:
            self.completed_frames = ("frame.exr",)
            self.retained_frames = (1,)
            self.is_finished = True

        @property
        def result(self) -> SimpleNamespace:
            return SimpleNamespace(frames=self.completed_frames)

    operator = ReportingOperator()
    window_manager = WindowManagerRecorder(fail_timer_remove=True)
    context = SimpleNamespace(window_manager=window_manager, window=object())
    controller = ExistingPassModalController(
        operator,
        cast(Any, runtime),
        cast(Any, settings),
    )
    controller.start(context, cast(Any, Session()))

    result = controller.handle_event(
        SimpleNamespace(type="TIMER", timer=window_manager.timer)
    )

    assert result == {"CANCELLED"}
    assert runtime.phase == "FAILED"
    assert operator.reports == [({"ERROR"}, "Cleanup failed: timer removal failed")]
