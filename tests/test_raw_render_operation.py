from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from object_datamosh.raw_render_operation import (
    RawRenderModalController,
    RenderEvent,
    RenderFrameRequest,
)


@dataclass
class RuntimeState:
    active: bool = False
    cancel_requested: bool = False
    phase: str = "IDLE"
    run_identity: str = ""
    current_frame: int = 0
    frame_start: int = 0
    frame_end: int = 0
    completed_work: int = 0
    total_work: int = 0
    progress: float = 0.0
    status: str = "Ready"


class WindowManager:
    def __init__(self) -> None:
        self.timer = object()
        self.events: list[tuple[str, object]] = []
        self.windows: tuple[object, ...] = ()

    def progress_begin(self, minimum: int, maximum: int) -> None:
        self.events.append(("progress_begin", (minimum, maximum)))

    def progress_update(self, value: int) -> None:
        self.events.append(("progress_update", value))

    def progress_end(self) -> None:
        self.events.append(("progress_end", None))

    def event_timer_add(self, interval: float, *, window: object) -> object:
        self.events.append(("timer_add", (interval, window)))
        return self.timer

    def event_timer_remove(self, timer: object) -> None:
        self.events.append(("timer_remove", timer))

    def modal_handler_add(self, operator: object) -> None:
        self.events.append(("modal_handler_add", operator))


class Operator:
    def __init__(self) -> None:
        self.reports: list[tuple[set[str], str]] = []

    def report(self, type: set[str], message: str) -> None:
        self.reports.append((type, message))


class FakeRenderSession:
    frame_start = 3
    frame_end = 4
    current_frame = 3
    completed_frames: tuple[object, ...] = ()
    is_finished = False

    def __init__(self) -> None:
        self.completed_requests: list[RenderFrameRequest] = []

    def prepare_next_frame(self) -> RenderFrameRequest:
        return RenderFrameRequest(
            frame=self.current_frame,
            scene=SimpleNamespace(name="Scene"),
            view_layer=SimpleNamespace(name="ViewLayer"),
        )

    def complete_frame(self, request: RenderFrameRequest) -> None:
        self.completed_requests.append(request)
        self.completed_frames += (request.frame,)
        self.current_frame += 1
        self.is_finished = self.current_frame > self.frame_end

    def close(self) -> None:
        pass


class FakeRenderAdapter:
    def __init__(self) -> None:
        self.launches: list[tuple[RenderFrameRequest, str]] = []
        self.event = RenderEvent.NONE

    def launch(self, request: RenderFrameRequest, run_identity: str) -> None:
        self.launches.append((request, run_identity))
        self.event = RenderEvent.ACTIVE

    def poll(self) -> RenderEvent:
        return self.event

    def remove(self) -> None:
        self.event = RenderEvent.NONE


def test_start_installs_one_modal_lifecycle() -> None:
    runtime = RuntimeState()
    settings = SimpleNamespace(status="Ready")
    operator = Operator()
    adapter = FakeRenderAdapter()
    window_manager = WindowManager()
    window = object()
    controller = RawRenderModalController(
        operator,
        runtime,
        settings,
        adapter=adapter,
        run_identity_factory=lambda: "raw-run",
    )

    controller.start(
        SimpleNamespace(window_manager=window_manager, window=window),
        FakeRenderSession(),
    )

    assert runtime.active
    assert runtime.phase == "RENDERING"
    assert runtime.current_frame == 3
    assert runtime.status == "Ready to render frame 3 of 4"
    assert adapter.launches == []
    assert window_manager.events == [
        ("progress_begin", (0, 2)),
        ("timer_add", (0.1, window)),
        ("progress_update", 0),
        ("modal_handler_add", operator),
    ]


def test_timer_launches_one_frame_and_advances_only_after_completion() -> None:
    runtime = RuntimeState()
    operator = Operator()
    adapter = FakeRenderAdapter()
    session = FakeRenderSession()
    window_manager = WindowManager()
    controller = RawRenderModalController(
        operator,
        runtime,
        SimpleNamespace(status="Ready"),
        adapter=adapter,
        run_identity_factory=lambda: "raw-run",
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        session,
    )
    timer = SimpleNamespace(type="TIMER", timer=window_manager.timer)

    assert controller.handle_event(timer) == {"RUNNING_MODAL"}
    assert [(request.frame, identity) for request, identity in adapter.launches] == [
        (3, "raw-run")
    ]
    assert session.completed_requests == []
    assert controller.handle_event(timer) == {"RUNNING_MODAL"}
    assert len(adapter.launches) == 1
    assert session.completed_requests == []

    adapter.event = RenderEvent.COMPLETED
    assert controller.handle_event(timer) == {"RUNNING_MODAL"}
    assert [request.frame for request in session.completed_requests] == [3]
    assert runtime.completed_work == 1
    assert runtime.current_frame == 4
    assert len(adapter.launches) == 1


def test_completed_last_frame_finalizes_and_releases_modal_resources() -> None:
    runtime = RuntimeState()
    operator = Operator()
    adapter = FakeRenderAdapter()
    session = FakeRenderSession()
    session.frame_end = 3
    window_manager = WindowManager()
    controller = RawRenderModalController(
        operator,
        runtime,
        SimpleNamespace(status="Ready"),
        adapter=adapter,
        run_identity_factory=lambda: "raw-run",
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        session,
    )
    timer = SimpleNamespace(type="TIMER", timer=window_manager.timer)
    assert controller.handle_event(timer) == {"RUNNING_MODAL"}
    adapter.event = RenderEvent.COMPLETED

    assert controller.handle_event(timer) == {"FINISHED"}

    assert not runtime.active
    assert runtime.phase == "COMPLETED"
    assert runtime.progress == 1.0
    assert operator.reports == [({"INFO"}, "Rendered 1 raw frame(s)")]
    assert window_manager.events[-2:] == [
        ("timer_remove", window_manager.timer),
        ("progress_end", None),
    ]


def test_cancellation_requested_during_preparation_prevents_launch() -> None:
    runtime = RuntimeState()

    class CancellingSession(FakeRenderSession):
        def prepare_next_frame(self) -> RenderFrameRequest:
            runtime.cancel_requested = True
            return super().prepare_next_frame()

    operator = Operator()
    adapter = FakeRenderAdapter()
    window_manager = WindowManager()
    controller = RawRenderModalController(
        operator,
        runtime,
        SimpleNamespace(status="Ready"),
        adapter=adapter,
        run_identity_factory=lambda: "raw-run",
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        CancellingSession(),
    )

    assert controller.handle_event(
        SimpleNamespace(type="TIMER", timer=window_manager.timer)
    ) == {"CANCELLED"}
    assert adapter.launches == []
    assert runtime.phase == "CANCELLED"


def test_escape_before_first_launch_cancels_without_rendering() -> None:
    runtime = RuntimeState()
    operator = Operator()
    adapter = FakeRenderAdapter()
    session = FakeRenderSession()
    window_manager = WindowManager()
    controller = RawRenderModalController(
        operator,
        runtime,
        SimpleNamespace(status="Ready"),
        adapter=adapter,
        run_identity_factory=lambda: "raw-run",
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        session,
    )

    assert controller.handle_event(SimpleNamespace(type="ESC")) == {"RUNNING_MODAL"}
    assert controller.handle_event(
        SimpleNamespace(type="TIMER", timer=window_manager.timer)
    ) == {"CANCELLED"}
    assert adapter.launches == []
    assert runtime.phase == "CANCELLED"


def test_escape_during_render_waits_for_completion_then_preserves_that_frame() -> None:
    runtime = RuntimeState()
    operator = Operator()
    adapter = FakeRenderAdapter()
    session = FakeRenderSession()
    window_manager = WindowManager()
    controller = RawRenderModalController(
        operator,
        runtime,
        SimpleNamespace(status="Ready"),
        adapter=adapter,
        run_identity_factory=lambda: "raw-run",
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        session,
    )
    timer = SimpleNamespace(type="TIMER", timer=window_manager.timer)
    assert controller.handle_event(timer) == {"RUNNING_MODAL"}

    assert controller.handle_event(SimpleNamespace(type="ESC")) == {"RUNNING_MODAL"}
    assert runtime.active
    assert runtime.phase == "CANCELLING"
    assert runtime.status == "Cancel requested; waiting for a safe boundary..."
    assert controller.handle_event(timer) == {"RUNNING_MODAL"}
    assert session.completed_requests == []

    adapter.event = RenderEvent.COMPLETED
    assert controller.handle_event(timer) == {"CANCELLED"}
    assert [request.frame for request in session.completed_requests] == [3]
    assert not runtime.active
    assert runtime.phase == "CANCELLED"
    assert operator.reports == [({"WARNING"}, "Cancelled after 1 frame(s)")]


def test_adapter_poll_failure_finalizes_the_active_frame() -> None:
    class FailingPollAdapter(FakeRenderAdapter):
        def poll(self) -> RenderEvent:
            raise RuntimeError("render event source failed")

    runtime = RuntimeState()
    operator = Operator()
    adapter = FailingPollAdapter()
    session = FakeRenderSession()
    window_manager = WindowManager()
    controller = RawRenderModalController(
        operator,
        runtime,
        SimpleNamespace(status="Ready"),
        adapter=adapter,
        run_identity_factory=lambda: "raw-run",
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        session,
    )
    timer = SimpleNamespace(type="TIMER", timer=window_manager.timer)

    assert controller.handle_event(timer) == {"CANCELLED"}
    assert not runtime.active
    assert runtime.phase == "FAILED"
    assert runtime.status == (
        "Raw rendering failed during rendering at frame 3: render event source failed"
    )


def test_output_verification_failure_reports_rendering_phase_and_frame() -> None:
    class FailingSession(FakeRenderSession):
        def complete_frame(self, request: RenderFrameRequest) -> None:
            raise RuntimeError("missing vector output")

    runtime = RuntimeState()
    operator = Operator()
    adapter = FakeRenderAdapter()
    session = FailingSession()
    window_manager = WindowManager()
    controller = RawRenderModalController(
        operator,
        runtime,
        SimpleNamespace(status="Ready"),
        adapter=adapter,
        run_identity_factory=lambda: "raw-run",
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        session,
    )
    timer = SimpleNamespace(type="TIMER", timer=window_manager.timer)
    assert controller.handle_event(timer) == {"RUNNING_MODAL"}
    adapter.event = RenderEvent.COMPLETED

    assert controller.handle_event(timer) == {"CANCELLED"}
    assert runtime.phase == "FAILED"
    assert runtime.status == (
        "Raw rendering failed during rendering at frame 3: missing vector output"
    )
    assert operator.reports == [
        ({"ERROR"}, "Raw rendering failed during rendering at frame 3: missing vector output")
    ]


def test_progress_update_failure_finalizes_and_releases_owned_resources() -> None:
    class FailingProgressWindowManager(WindowManager):
        def progress_update(self, value: int) -> None:
            super().progress_update(value)
            if value:
                raise RuntimeError("progress display unavailable")

    runtime = RuntimeState()
    operator = Operator()
    adapter = FakeRenderAdapter()
    session = FakeRenderSession()
    window_manager = FailingProgressWindowManager()
    controller = RawRenderModalController(
        operator,
        runtime,
        SimpleNamespace(status="Ready"),
        adapter=adapter,
        run_identity_factory=lambda: "raw-run",
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        session,
    )
    timer = SimpleNamespace(type="TIMER", timer=window_manager.timer)
    assert controller.handle_event(timer) == {"RUNNING_MODAL"}
    adapter.event = RenderEvent.COMPLETED

    assert controller.handle_event(timer) == {"CANCELLED"}
    assert not runtime.active
    assert runtime.phase == "FAILED"
    assert runtime.status == (
        "Raw rendering failed during rendering at frame 3: progress display unavailable"
    )
    assert window_manager.events[-2:] == [
        ("timer_remove", window_manager.timer),
        ("progress_end", None),
    ]


def test_session_close_failure_still_runs_controller_cleanup() -> None:
    class FailingCloseSession(FakeRenderSession):
        frame_end = 3

        def close(self) -> None:
            raise RuntimeError("could not restore output paths")

    runtime = RuntimeState()
    operator = Operator()
    adapter = FakeRenderAdapter()
    session = FailingCloseSession()
    window_manager = WindowManager()
    cleanup_calls: list[str] = []
    controller = RawRenderModalController(
        operator,
        runtime,
        SimpleNamespace(status="Ready"),
        adapter=adapter,
        on_cleanup=lambda: cleanup_calls.append("cleared"),
        run_identity_factory=lambda: "raw-run",
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        session,
    )
    timer = SimpleNamespace(type="TIMER", timer=window_manager.timer)
    assert controller.handle_event(timer) == {"RUNNING_MODAL"}
    adapter.event = RenderEvent.COMPLETED

    assert controller.handle_event(timer) == {"CANCELLED"}
    assert cleanup_calls == ["cleared"]
    assert not runtime.active
    assert runtime.phase == "FAILED"
    assert runtime.status == (
        "Rendered 1 raw frame(s); cleanup failed during finalization at frame 3: "
        "could not restore output paths"
    )
    assert operator.reports == [
        (
            {"ERROR"},
            "Rendered 1 raw frame(s); cleanup failed during finalization at frame 3: "
            "could not restore output paths",
        )
    ]


def test_blender_cancel_event_does_not_verify_or_retain_the_active_frame() -> None:
    runtime = RuntimeState()
    operator = Operator()
    adapter = FakeRenderAdapter()
    session = FakeRenderSession()
    window_manager = WindowManager()
    controller = RawRenderModalController(
        operator,
        runtime,
        SimpleNamespace(status="Ready"),
        adapter=adapter,
        run_identity_factory=lambda: "raw-run",
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        session,
    )
    timer = SimpleNamespace(type="TIMER", timer=window_manager.timer)
    assert controller.handle_event(timer) == {"RUNNING_MODAL"}
    adapter.event = RenderEvent.CANCELLED

    assert controller.handle_event(timer) == {"CANCELLED"}
    assert session.completed_requests == []
    assert runtime.phase == "CANCELLED"
    assert operator.reports == [({"WARNING"}, "Cancelled after 0 frame(s)")]
