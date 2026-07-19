from types import SimpleNamespace
from typing import Any, cast

from object_datamosh.existing_pass_operation import ExistingPassModalController


class WindowManagerRecorder:
    def __init__(self) -> None:
        self.timer = object()
        self.windows: tuple[object, ...] = ()

    def progress_begin(self, _minimum: int, _maximum: int) -> None:
        pass

    def progress_update(self, _value: int) -> None:
        pass

    def progress_end(self) -> None:
        pass

    def event_timer_add(self, _interval: float, *, window: object) -> object:
        del window
        return self.timer

    def event_timer_remove(self, timer: object) -> None:
        assert timer is self.timer

    def modal_handler_add(self, _operator: object) -> None:
        pass


class ReportingOperator:
    def report(self, type: Any, message: str) -> None:
        del type, message


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
