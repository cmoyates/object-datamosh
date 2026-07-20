from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from object_datamosh.core.paths import FramePaths
from object_datamosh.raw_render import RenderFrameRequest
from object_datamosh.raw_render_operation import RenderEvent
from object_datamosh.render_and_process_operation import (
    RenderAndProcessModalController,
    RenderAndProcessState,
    RenderAndProcessStateMachine,
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
    phase_completed_work: int = 0
    phase_total_work: int = 0
    progress: float = 0.0
    status: str = "Ready"


@dataclass
class InvalidatableRuntime(RuntimeState):
    available: bool = True

    def invalidate(self) -> None:
        self.available = False

    def __getattribute__(self, name: str) -> Any:
        if name not in {"available", "invalidate", "__dict__", "__class__"}:
            try:
                available = object.__getattribute__(self, "available")
            except AttributeError:
                available = True
            if not available:
                raise ReferenceError("scene RNA was removed")
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: object) -> None:
        if name != "available" and not getattr(self, "available", True):
            raise ReferenceError("scene RNA was removed")
        object.__setattr__(self, name, value)


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


class RenderSession:
    frame_start = 3
    frame_end = 4

    def __init__(self, output_frames: tuple[FramePaths, ...] = ()) -> None:
        self.output_frames = output_frames
        self.current_frame = self.frame_start
        self.completed_frames: tuple[FramePaths, ...] = ()
        self.is_finished = False

    def prepare_next_frame(self) -> RenderFrameRequest:
        return RenderFrameRequest(
            frame=self.current_frame,
            scene=SimpleNamespace(name="Scene"),
            view_layer=SimpleNamespace(name="ViewLayer"),
        )

    def complete_frame(self, request: RenderFrameRequest) -> FramePaths:
        index = request.frame - self.frame_start
        if not self.output_frames:
            raise RuntimeError("the test render session has no discovered outputs")
        completed = self.output_frames[index]
        self.completed_frames = (*self.completed_frames, completed)
        self.current_frame += 1
        self.is_finished = self.current_frame > self.frame_end
        return completed

    def close(self) -> None:
        pass


class RenderAdapter:
    def __init__(self) -> None:
        self.event = RenderEvent.NONE
        self.launches: list[int] = []

    def launch(self, request: RenderFrameRequest, run_identity: str) -> None:
        self.launches.append(request.frame)
        self.event = RenderEvent.ACTIVE

    def poll(self) -> RenderEvent:
        return self.event

    def remove(self) -> None:
        self.event = RenderEvent.NONE


@dataclass(frozen=True)
class ProcessingResult:
    frames: tuple[Path, ...]


class ProcessingSession:
    frame_start = 3
    frame_end = 4
    recovery_frame = None
    configuration_name = "Full Frame / Trail"
    manifest_path = Path("/output/processed/ODM_sequence_manifest.json")

    def __init__(self, outputs: tuple[Path, ...]) -> None:
        self.outputs = outputs
        self.current_frame = self.frame_start
        self.completed_frames: tuple[Path, ...] = ()
        self.is_finished = False

    @property
    def result(self) -> ProcessingResult:
        return ProcessingResult(frames=self.completed_frames)

    def process_next_frame(self) -> None:
        index = self.current_frame - self.frame_start
        self.completed_frames = (*self.completed_frames, self.outputs[index])
        if self.current_frame == self.frame_end:
            self.is_finished = True
        else:
            self.current_frame += 1


class FailingProcessingSession(ProcessingSession):
    def process_next_frame(self) -> None:
        raise RuntimeError("image write failed")


def test_combined_workflow_starts_in_initialization_with_no_completed_work() -> None:
    workflow = RenderAndProcessStateMachine(frame_start=3, frame_end=5)

    assert workflow.state is RenderAndProcessState.INITIALIZING
    assert workflow.current_frame == 3
    assert workflow.rendered_count == 0
    assert workflow.processed_count == 0
    assert workflow.completed_work == 0
    assert workflow.total_work == 6
    assert workflow.progress == 0.0


def test_rendering_advances_one_frame_and_updates_overall_progress() -> None:
    workflow = RenderAndProcessStateMachine(frame_start=3, frame_end=5)

    workflow.begin_rendering()
    workflow.record_rendered_frame(3)

    assert workflow.state is RenderAndProcessState.RENDERING
    assert workflow.current_frame == 4
    assert workflow.rendered_count == 1
    assert workflow.completed_work == 1
    assert workflow.progress == 1 / 6


def test_completed_rendering_transitions_to_processing_without_resetting_progress() -> None:
    workflow = RenderAndProcessStateMachine(frame_start=3, frame_end=4)
    workflow.begin_rendering()
    workflow.record_rendered_frame(3)
    workflow.record_rendered_frame(4)

    workflow.begin_processing()

    assert workflow.state is RenderAndProcessState.PROCESSING
    assert workflow.current_frame == 3
    assert workflow.rendered_count == 2
    assert workflow.completed_work == 2
    assert workflow.progress == 0.5


def test_processing_advances_to_successful_completion() -> None:
    workflow = RenderAndProcessStateMachine(frame_start=8, frame_end=9)
    workflow.begin_rendering()
    workflow.record_rendered_frame(8)
    workflow.record_rendered_frame(9)
    workflow.begin_processing()

    workflow.record_processed_frame(8)
    workflow.record_processed_frame(9)
    workflow.complete()
    assert workflow.state is RenderAndProcessState.FINALIZING
    workflow.finish(RenderAndProcessState.COMPLETED)

    assert workflow.state is RenderAndProcessState.COMPLETED
    assert workflow.rendered_count == 2
    assert workflow.processed_count == 2
    assert workflow.completed_work == 4
    assert workflow.progress == 1.0


def test_state_machine_rejects_work_beyond_the_configured_range() -> None:
    workflow = RenderAndProcessStateMachine(frame_start=3, frame_end=3)
    workflow.begin_rendering()
    workflow.record_rendered_frame(3)

    try:
        workflow.record_rendered_frame(4)
    except RuntimeError as error:
        assert "no remaining frames" in str(error)
    else:
        raise AssertionError("rendering advanced beyond its configured range")

    workflow.begin_processing()
    workflow.record_processed_frame(3)
    try:
        workflow.record_processed_frame(4)
    except RuntimeError as error:
        assert "no remaining frames" in str(error)
    else:
        raise AssertionError("processing advanced beyond its configured range")

    assert workflow.completed_work == workflow.total_work == 2
    assert workflow.progress == 1.0


def test_cancellation_preserves_completed_phase_work() -> None:
    workflow = RenderAndProcessStateMachine(frame_start=1, frame_end=2)
    workflow.begin_rendering()
    workflow.record_rendered_frame(1)

    workflow.cancel()
    assert workflow.state is RenderAndProcessState.FINALIZING
    workflow.finish(RenderAndProcessState.CANCELLED)

    assert workflow.state is RenderAndProcessState.CANCELLED
    assert workflow.completed_work == 1
    assert workflow.progress == 0.25


def test_failed_workflow_cannot_advance_after_finalization() -> None:
    workflow = RenderAndProcessStateMachine(frame_start=1, frame_end=2)
    workflow.begin_rendering()
    workflow.fail()
    assert workflow.state is RenderAndProcessState.FINALIZING

    workflow.record_rendered_frame(1)
    workflow.finish(RenderAndProcessState.FAILED)

    assert workflow.state is RenderAndProcessState.FAILED
    assert workflow.completed_work == 0


def test_combined_controller_starts_one_modal_lifecycle_for_both_phases() -> None:
    runtime = RuntimeState()
    settings = SimpleNamespace(status="Ready")
    operator = Operator()
    window_manager = WindowManager()
    window = object()
    controller = RenderAndProcessModalController(
        operator,
        runtime,
        settings,
        adapter=RenderAdapter(),
        create_processing=lambda _frames, _should_cancel: ProcessingSession(
            (Path("unused-3"), Path("unused-4"))
        ),
        run_identity_factory=lambda: "combined-run",
    )

    controller.start(
        SimpleNamespace(window_manager=window_manager, window=window),
        RenderSession(),
    )

    assert runtime.active
    assert runtime.phase == "RENDERING"
    assert runtime.current_frame == 3
    assert runtime.completed_work == 0
    assert runtime.total_work == 4
    assert runtime.phase_completed_work == 0
    assert runtime.phase_total_work == 2
    assert runtime.status == "Ready to render frame 3 of 4"
    assert window_manager.events == [
        ("progress_begin", (0, 4)),
        ("timer_add", (0.1, window)),
        ("progress_update", 0),
        ("modal_handler_add", operator),
    ]


def test_rendering_transition_passes_exact_discovered_frames_to_processing(
    tmp_path: Path,
) -> None:
    raw_frames = (
        FramePaths(
            3,
            tmp_path / "beauty-a",
            tmp_path / "vector-a",
            tmp_path / "matte-a",
            tmp_path / "out-a",
        ),
        FramePaths(
            4,
            tmp_path / "beauty-b",
            tmp_path / "vector-b",
            tmp_path / "matte-b",
            tmp_path / "out-b",
        ),
    )
    received: list[tuple[FramePaths, ...]] = []
    runtime = RuntimeState()
    settings = SimpleNamespace(status="Ready")
    operator = Operator()
    window_manager = WindowManager()
    adapter = RenderAdapter()

    def create_processing(
        frames: tuple[FramePaths, ...], _should_cancel: object
    ) -> ProcessingSession:
        received.append(frames)
        return ProcessingSession((tmp_path / "p3", tmp_path / "p4"))

    controller = RenderAndProcessModalController(
        operator,
        runtime,
        settings,
        adapter=adapter,
        create_processing=create_processing,
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        RenderSession(raw_frames),
    )
    timer_event = SimpleNamespace(type="TIMER", timer=window_manager.timer)

    assert controller.handle_event(timer_event) == {"RUNNING_MODAL"}
    adapter.event = RenderEvent.COMPLETED
    assert controller.handle_event(timer_event) == {"RUNNING_MODAL"}
    assert controller.handle_event(timer_event) == {"RUNNING_MODAL"}
    adapter.event = RenderEvent.COMPLETED
    assert controller.handle_event(timer_event) == {"RUNNING_MODAL"}

    assert received == [raw_frames]
    assert received[0][0] is raw_frames[0]
    assert received[0][1] is raw_frames[1]
    assert runtime.phase == "PROCESSING"
    assert runtime.current_frame == 3
    assert runtime.completed_work == 2
    assert runtime.total_work == 4
    assert runtime.phase_completed_work == 0
    assert runtime.phase_total_work == 2
    assert runtime.progress == 0.5
    assert runtime.status == "Processing: Full Frame / Trail (frame 3 of 4)"


def test_processing_advances_one_frame_per_timer_and_finalizes_the_shared_lifecycle(
    tmp_path: Path,
) -> None:
    raw_frames = (
        FramePaths(3, tmp_path / "b3", tmp_path / "v3", tmp_path / "m3", tmp_path / "p3"),
        FramePaths(4, tmp_path / "b4", tmp_path / "v4", tmp_path / "m4", tmp_path / "p4"),
    )
    runtime = RuntimeState()
    settings = SimpleNamespace(status="Ready")
    operator = Operator()
    window_manager = WindowManager()
    adapter = RenderAdapter()
    processing = ProcessingSession((tmp_path / "p3", tmp_path / "p4"))
    controller = RenderAndProcessModalController(
        operator,
        runtime,
        settings,
        adapter=adapter,
        create_processing=lambda _frames, _should_cancel: processing,
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        RenderSession(raw_frames),
    )
    event = SimpleNamespace(type="TIMER", timer=window_manager.timer)
    controller.handle_event(event)
    adapter.event = RenderEvent.COMPLETED
    controller.handle_event(event)
    controller.handle_event(event)
    adapter.event = RenderEvent.COMPLETED
    controller.handle_event(event)

    assert controller.handle_event(event) == {"RUNNING_MODAL"}
    assert processing.completed_frames == (tmp_path / "p3",)
    assert runtime.completed_work == 3
    assert runtime.progress == 0.75
    assert controller.handle_event(event) == {"FINISHED"}

    assert processing.completed_frames == (tmp_path / "p3", tmp_path / "p4")
    assert runtime.active is False
    assert runtime.phase == "COMPLETED"
    assert runtime.completed_work == 4
    assert runtime.phase_completed_work == 2
    assert runtime.phase_total_work == 2
    assert runtime.progress == 1.0
    assert runtime.status == (
        "Render and Process complete: 2 frame(s) with Full Frame / Trail; report: "
        "/output/processed/ODM_sequence_manifest.json"
    )
    assert settings.status == runtime.status
    assert window_manager.events.count(("timer_remove", window_manager.timer)) == 1
    assert window_manager.events.count(("progress_end", None)) == 1


def test_escape_during_rendering_cancels_at_the_next_boundary_and_preserves_progress(
    tmp_path: Path,
) -> None:
    raw_frames = (
        FramePaths(3, tmp_path / "b3", tmp_path / "v3", tmp_path / "m3", tmp_path / "p3"),
        FramePaths(4, tmp_path / "b4", tmp_path / "v4", tmp_path / "m4", tmp_path / "p4"),
    )
    runtime = RuntimeState()
    settings = SimpleNamespace(status="Ready")
    operator = Operator()
    window_manager = WindowManager()
    adapter = RenderAdapter()
    render_session = RenderSession(raw_frames)
    controller = RenderAndProcessModalController(
        operator,
        runtime,
        settings,
        adapter=adapter,
        create_processing=lambda _frames, _should_cancel: ProcessingSession(
            (tmp_path / "p3", tmp_path / "p4")
        ),
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        render_session,
    )
    timer = SimpleNamespace(type="TIMER", timer=window_manager.timer)
    controller.handle_event(timer)
    adapter.event = RenderEvent.COMPLETED
    controller.handle_event(timer)

    assert controller.handle_event(SimpleNamespace(type="ESC")) == {"RUNNING_MODAL"}
    assert runtime.phase == "CANCELLING"
    assert controller.handle_event(timer) == {"CANCELLED"}

    assert render_session.completed_frames == (raw_frames[0],)
    assert runtime.active is False
    assert runtime.phase == "CANCELLED"
    assert runtime.completed_work == 1
    assert runtime.status == "Render and Process cancelled after 1 of 4 steps"


def test_cancel_request_during_processing_stops_before_another_frame(tmp_path: Path) -> None:
    raw_frames = (
        FramePaths(3, tmp_path / "b3", tmp_path / "v3", tmp_path / "m3", tmp_path / "p3"),
        FramePaths(4, tmp_path / "b4", tmp_path / "v4", tmp_path / "m4", tmp_path / "p4"),
    )
    runtime = RuntimeState()
    settings = SimpleNamespace(status="Ready")
    operator = Operator()
    window_manager = WindowManager()
    adapter = RenderAdapter()
    processing = ProcessingSession((tmp_path / "p3", tmp_path / "p4"))
    controller = RenderAndProcessModalController(
        operator,
        runtime,
        settings,
        adapter=adapter,
        create_processing=lambda _frames, _should_cancel: processing,
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        RenderSession(raw_frames),
    )
    timer = SimpleNamespace(type="TIMER", timer=window_manager.timer)
    controller.handle_event(timer)
    adapter.event = RenderEvent.COMPLETED
    controller.handle_event(timer)
    controller.handle_event(timer)
    adapter.event = RenderEvent.COMPLETED
    controller.handle_event(timer)
    controller.handle_event(timer)

    assert controller.handle_event(SimpleNamespace(type="ESC")) == {"RUNNING_MODAL"}
    assert runtime.status == "Cancel requested; waiting for a safe boundary..."
    assert controller.handle_event(timer) == {"CANCELLED"}

    assert processing.completed_frames == (tmp_path / "p3",)
    assert runtime.active is False
    assert runtime.phase == "CANCELLED"
    assert runtime.completed_work == 3
    assert runtime.phase_completed_work == 1
    assert runtime.phase_total_work == 2
    assert runtime.status == "Render and Process cancelled after 3 of 4 steps"


def test_render_failure_reports_the_combined_phase_and_frame() -> None:
    runtime = RuntimeState()
    settings = SimpleNamespace(status="Ready")
    operator = Operator()
    window_manager = WindowManager()
    adapter = RenderAdapter()
    controller = RenderAndProcessModalController(
        operator,
        runtime,
        settings,
        adapter=adapter,
        create_processing=lambda _frames, _should_cancel: ProcessingSession(
            (Path("unused-3"), Path("unused-4"))
        ),
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        RenderSession(),
    )
    event = SimpleNamespace(type="TIMER", timer=window_manager.timer)
    controller.handle_event(event)
    adapter.event = RenderEvent.FAILED

    assert controller.handle_event(event) == {"CANCELLED"}

    assert runtime.phase == "FAILED"
    assert runtime.status == (
        "Render and Process failed during rendering at frame 3: Blender render failed"
    )
    assert operator.reports[-1] == ({"ERROR"}, runtime.status)


def test_render_failure_retains_authoritative_completed_raw_work(tmp_path: Path) -> None:
    raw_frames = (
        FramePaths(3, tmp_path / "b3", tmp_path / "v3", tmp_path / "m3", tmp_path / "p3"),
        FramePaths(4, tmp_path / "b4", tmp_path / "v4", tmp_path / "m4", tmp_path / "p4"),
    )
    runtime = RuntimeState()
    settings = SimpleNamespace(status="Ready")
    operator = Operator()
    window_manager = WindowManager()
    adapter = RenderAdapter()
    controller = RenderAndProcessModalController(
        operator,
        runtime,
        settings,
        adapter=adapter,
        create_processing=lambda _frames, _should_cancel: ProcessingSession(
            (tmp_path / "p3", tmp_path / "p4")
        ),
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        RenderSession(raw_frames),
    )
    timer = SimpleNamespace(type="TIMER", timer=window_manager.timer)
    controller.handle_event(timer)
    adapter.event = RenderEvent.COMPLETED
    controller.handle_event(timer)
    controller.handle_event(timer)
    adapter.event = RenderEvent.FAILED

    assert controller.handle_event(timer) == {"CANCELLED"}
    assert not runtime.active
    assert runtime.phase == "FAILED"
    assert runtime.completed_work == 1
    assert runtime.phase_completed_work == 1
    assert runtime.status == (
        "Render and Process failed during rendering at frame 4: Blender render failed"
    )


def test_processing_failure_reports_the_combined_phase_and_frame(tmp_path: Path) -> None:
    raw_frames = (
        FramePaths(3, tmp_path / "b3", tmp_path / "v3", tmp_path / "m3", tmp_path / "p3"),
        FramePaths(4, tmp_path / "b4", tmp_path / "v4", tmp_path / "m4", tmp_path / "p4"),
    )
    runtime = RuntimeState()
    settings = SimpleNamespace(status="Ready")
    operator = Operator()
    window_manager = WindowManager()
    adapter = RenderAdapter()
    processing = FailingProcessingSession((tmp_path / "p3", tmp_path / "p4"))
    controller = RenderAndProcessModalController(
        operator,
        runtime,
        settings,
        adapter=adapter,
        create_processing=lambda _frames, _should_cancel: processing,
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        RenderSession(raw_frames),
    )
    event = SimpleNamespace(type="TIMER", timer=window_manager.timer)
    controller.handle_event(event)
    adapter.event = RenderEvent.COMPLETED
    controller.handle_event(event)
    controller.handle_event(event)
    adapter.event = RenderEvent.COMPLETED
    controller.handle_event(event)

    assert controller.handle_event(event) == {"CANCELLED"}

    assert runtime.phase == "FAILED"
    assert runtime.status == (
        "Render and Process failed during processing at frame 3: image write failed"
    )
    assert operator.reports[-1] == ({"ERROR"}, runtime.status)


def test_cancel_with_removed_runtime_prevents_another_raw_launch(tmp_path: Path) -> None:
    raw_frames = (
        FramePaths(3, tmp_path / "b3", tmp_path / "v3", tmp_path / "m3", tmp_path / "p3"),
        FramePaths(4, tmp_path / "b4", tmp_path / "v4", tmp_path / "m4", tmp_path / "p4"),
    )
    runtime = InvalidatableRuntime()
    settings = SimpleNamespace(status="Ready")
    operator = Operator()
    window_manager = WindowManager()
    adapter = RenderAdapter()
    controller = RenderAndProcessModalController(
        operator,
        runtime,
        settings,
        adapter=adapter,
        create_processing=lambda _frames, _should_cancel: ProcessingSession(
            (tmp_path / "p3", tmp_path / "p4")
        ),
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        RenderSession(raw_frames),
    )
    timer = SimpleNamespace(type="TIMER", timer=window_manager.timer)
    controller.handle_event(timer)
    adapter.event = RenderEvent.COMPLETED
    controller.handle_event(timer)
    runtime.invalidate()

    assert controller.request_cancel()
    assert controller.handle_event(timer) == {"CANCELLED"}
    assert adapter.launches == [3]
    assert window_manager.events.count(("timer_remove", window_manager.timer)) == 1
    assert window_manager.events.count(("progress_end", None)) == 1


def test_removed_scene_runtime_still_finalizes_modal_resources(tmp_path: Path) -> None:
    raw_frames = (
        FramePaths(3, tmp_path / "b3", tmp_path / "v3", tmp_path / "m3", tmp_path / "p3"),
        FramePaths(4, tmp_path / "b4", tmp_path / "v4", tmp_path / "m4", tmp_path / "p4"),
    )
    runtime = InvalidatableRuntime()
    settings = SimpleNamespace(status="Ready")
    operator = Operator()
    window_manager = WindowManager()
    adapter = RenderAdapter()
    cleanup_events: list[str] = []
    controller = RenderAndProcessModalController(
        operator,
        runtime,
        settings,
        adapter=adapter,
        create_processing=lambda _frames, _should_cancel: ProcessingSession(
            (tmp_path / "p3", tmp_path / "p4")
        ),
        on_cleanup=lambda: cleanup_events.append("cleanup"),
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        RenderSession(raw_frames),
    )
    timer = SimpleNamespace(type="TIMER", timer=window_manager.timer)
    controller.handle_event(timer)
    adapter.event = RenderEvent.COMPLETED
    controller.handle_event(timer)
    controller.handle_event(timer)
    adapter.event = RenderEvent.COMPLETED
    controller.handle_event(timer)
    runtime.invalidate()

    assert controller.handle_event(timer) == {"CANCELLED"}
    assert cleanup_events == ["cleanup"]
    assert window_manager.events.count(("timer_remove", window_manager.timer)) == 1
    assert window_manager.events.count(("progress_end", None)) == 1
    assert operator.reports[-1][0] == {"ERROR"}
    assert "failed during processing at frame 3" in operator.reports[-1][1]


def test_external_cancel_during_rendering_retains_discovered_progress(tmp_path: Path) -> None:
    raw_frames = (
        FramePaths(3, tmp_path / "b3", tmp_path / "v3", tmp_path / "m3", tmp_path / "p3"),
        FramePaths(4, tmp_path / "b4", tmp_path / "v4", tmp_path / "m4", tmp_path / "p4"),
    )
    runtime = RuntimeState()
    settings = SimpleNamespace(status="Ready")
    operator = Operator()
    window_manager = WindowManager()
    adapter = RenderAdapter()
    controller = RenderAndProcessModalController(
        operator,
        runtime,
        settings,
        adapter=adapter,
        create_processing=lambda _frames, _should_cancel: ProcessingSession(
            (tmp_path / "p3", tmp_path / "p4")
        ),
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        RenderSession(raw_frames),
    )
    timer = SimpleNamespace(type="TIMER", timer=window_manager.timer)
    controller.handle_event(timer)
    adapter.event = RenderEvent.COMPLETED
    controller.handle_event(timer)

    controller.cancel()

    assert not runtime.active
    assert runtime.phase == "CANCELLED"
    assert runtime.completed_work == 1
    assert runtime.phase_completed_work == 1
    assert runtime.phase_total_work == 2
    assert runtime.status == "Render and Process cancelled after 1 of 4 steps"
    assert window_manager.events.count(("timer_remove", window_manager.timer)) == 1
    assert window_manager.events.count(("progress_end", None)) == 1


def test_transition_failure_uses_shared_finalizer_and_reports_phase(tmp_path: Path) -> None:
    raw_frames = (
        FramePaths(3, tmp_path / "b3", tmp_path / "v3", tmp_path / "m3", tmp_path / "p3"),
        FramePaths(4, tmp_path / "b4", tmp_path / "v4", tmp_path / "m4", tmp_path / "p4"),
    )
    runtime = RuntimeState()
    settings = SimpleNamespace(status="Ready")
    operator = Operator()
    window_manager = WindowManager()
    adapter = RenderAdapter()

    def fail_transition(
        _frames: tuple[FramePaths, ...], _should_cancel: object
    ) -> ProcessingSession:
        raise RuntimeError("processor initialization failed")

    controller = RenderAndProcessModalController(
        operator,
        runtime,
        settings,
        adapter=adapter,
        create_processing=fail_transition,
    )
    controller.start(
        SimpleNamespace(window_manager=window_manager, window=object()),
        RenderSession(raw_frames),
    )
    timer = SimpleNamespace(type="TIMER", timer=window_manager.timer)
    controller.handle_event(timer)
    adapter.event = RenderEvent.COMPLETED
    controller.handle_event(timer)
    controller.handle_event(timer)
    adapter.event = RenderEvent.COMPLETED

    assert controller.handle_event(timer) == {"CANCELLED"}
    assert not runtime.active
    assert runtime.phase == "FAILED"
    assert runtime.completed_work == 2
    assert runtime.status == (
        "Render and Process failed during transition to processing at frame 3: "
        "processor initialization failed"
    )
    assert window_manager.events.count(("timer_remove", window_manager.timer)) == 1
    assert window_manager.events.count(("progress_end", None)) == 1
    assert operator.reports[-1] == ({"ERROR"}, runtime.status)
