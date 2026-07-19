from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, TypedDict, cast

import bpy
from bpy.types import Context, Object, Scene, Screen

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import object_datamosh  # noqa: E402
from object_datamosh.core.paths import SequencePaths  # noqa: E402
from object_datamosh.ui import (  # noqa: E402
    ODM_PT_sidebar,
    runtime_for_scene,
    settings_for_scene,
)

configured_work_root = os.environ.get("ODM_ISSUE26_WORK_ROOT")
WORK_ROOT = (
    Path(configured_work_root)
    if configured_work_root
    else Path(tempfile.mkdtemp(prefix="object-datamosh-issue26-"))
)
ROOT = WORK_ROOT / "output"
LOG = WORK_ROOT / "events.jsonl"
RESULT = Path(os.environ.get("ODM_ISSUE26_RESULT", WORK_ROOT / "result.json"))
WORK_ROOT.mkdir(parents=True, exist_ok=True)
RESULT.parent.mkdir(parents=True, exist_ok=True)
shutil.rmtree(ROOT, ignore_errors=True)
LOG.unlink(missing_ok=True)
RESULT.unlink(missing_ok=True)


def emit(event: str, **values: object) -> None:
    record = {"time": round(time.monotonic(), 6), "event": event, **values}
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
        stream.flush()


class HandlerRegistry(Protocol):
    render_pre: list[Callable[..., None]]
    render_complete: list[Callable[..., None]]
    render_cancel: list[Callable[..., None]]


class TimerRegistry(Protocol):
    def register(
        self,
        function: Callable[[], float | None],
        *,
        first_interval: float,
        persistent: bool,
    ) -> None: ...


class AppFacade(Protocol):
    build_hash: bytes | str
    version_string: str
    driver_namespace: dict[str, object]
    handlers: HandlerRegistry
    timers: TimerRegistry


class EventWindow(Protocol):
    def event_simulate(self, *, type: str, value: str, x: int, y: int) -> None: ...


class ContextFacade(Protocol):
    scene: Scene
    active_object: Object | None
    screen: Screen
    window: EventWindow


class ObjectDatamoshOperations(Protocol):
    def render_and_process(self, execution_context: str) -> set[str]: ...
    def process_sequence(self, execution_context: str) -> set[str]: ...
    def setup_object_index(self) -> set[str]: ...
    def cancel_operation(self) -> set[str]: ...


class WindowManagerOperations(Protocol):
    def quit_blender(self) -> set[str]: ...


_app = cast(AppFacade, bpy.app)
_context = cast(ContextFacade, bpy.context)
_dynamic_ops = cast(Any, bpy.ops)
_odm_ops = cast(ObjectDatamoshOperations, _dynamic_ops.object_datamosh)
_wm_ops = cast(WindowManagerOperations, bpy.ops.wm)


class ProbeStage(StrEnum):
    INITIALIZE = "initialize"
    COMBINED_SUCCESS = "combined_success"
    RAW_BUTTON_CANCEL = "raw_button_cancel"
    RAW_ESCAPE_CANCEL = "raw_escape_cancel"
    PROCESSING_CANCEL = "processing_cancel"
    PROCESSING_RESUME = "processing_resume"
    PROCESSING_ESCAPE_CANCEL = "processing_escape_cancel"
    PROCESSING_ESCAPE_RESUME = "processing_escape_resume"
    RESTART_AFTER_RESUME = "restart_after_resume"


class Snapshot(TypedDict):
    stage: str
    active: bool
    cancel_requested: bool
    phase: str
    current_frame: int
    completed_work: int
    total_work: int
    phase_completed_work: int
    phase_total_work: int
    progress: float
    runtime_status: str
    settings_status: str
    scene_frame: int


SidebarObservation = tuple[str, str, int, int, int, int, int, float]


class Evidence(TypedDict, total=False):
    combined_success: dict[str, object]
    raw_button_cancel: dict[str, object]
    raw_escape_cancel: dict[str, object]
    processing_button_cancel: dict[str, object]
    processing_escape_cancel: dict[str, object]
    processing_resumes_completed: bool
    immediate_restart_completed: bool


_ALLOWED_TRANSITIONS = {
    ProbeStage.INITIALIZE: ProbeStage.COMBINED_SUCCESS,
    ProbeStage.COMBINED_SUCCESS: ProbeStage.RAW_BUTTON_CANCEL,
    ProbeStage.RAW_BUTTON_CANCEL: ProbeStage.RAW_ESCAPE_CANCEL,
    ProbeStage.RAW_ESCAPE_CANCEL: ProbeStage.PROCESSING_CANCEL,
    ProbeStage.PROCESSING_CANCEL: ProbeStage.PROCESSING_RESUME,
    ProbeStage.PROCESSING_RESUME: ProbeStage.PROCESSING_ESCAPE_CANCEL,
    ProbeStage.PROCESSING_ESCAPE_CANCEL: ProbeStage.PROCESSING_ESCAPE_RESUME,
    ProbeStage.PROCESSING_ESCAPE_RESUME: ProbeStage.RESTART_AFTER_RESUME,
}


@dataclass
class ProbeState:
    stage: ProbeStage = ProbeStage.INITIALIZE
    snapshots: list[Snapshot] = field(default_factory=list)
    seen: set[tuple[Any, ...]] = field(default_factory=set)
    original_frame: int = 7
    render_active: bool = False
    heartbeat_count: int = 0
    heartbeats_during_render: int = 0
    sidebar_draws: list[SidebarObservation] = field(default_factory=list)
    evidence: Evidence = field(default_factory=Evidence)
    baseline_complete_handlers: int = 0
    baseline_cancel_handlers: int = 0
    cancel_sent: bool = False
    escape_seen: bool = False
    processing_cancel_sent: bool = False
    processing_escape_seen: bool = False
    restart_cancel_sent: bool = False
    scene_initialized: bool = False
    category_click_offset: int = 10
    cancel_click_offset: int = 20
    last_click_coordinate: tuple[int, int] | None = None
    cancel_button_coordinate: tuple[int, int] | None = None
    processing_click_injected: bool = False

    def transition(self, next_stage: ProbeStage) -> None:
        expected = _ALLOWED_TRANSITIONS.get(self.stage)
        if next_stage is not expected:
            raise RuntimeError(f"Invalid foreground-probe transition: {self.stage} -> {next_stage}")
        self.stage = next_stage


state = ProbeState()


class SidebarRegion(Protocol):
    type: str
    x: int
    y: int
    width: int
    height: int
    active_panel_category: str


def visible_sidebar_region() -> SidebarRegion:
    for area in _context.screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if region.type == "UI":
                return cast(SidebarRegion, region)
    raise RuntimeError("No visible 3D View sidebar region is available")


def simulate_left_click(x: int, y: int) -> None:
    _context.window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=x, y=y)
    _context.window.event_simulate(type="LEFTMOUSE", value="PRESS", x=x, y=y)
    _context.window.event_simulate(type="LEFTMOUSE", value="RELEASE", x=x, y=y)


def verified_cancel_button_coordinate() -> tuple[int, int]:
    coordinate = state.cancel_button_coordinate
    if coordinate is None:
        raise RuntimeError("The production Cancel button coordinate was not verified")
    return coordinate


def move_pointer_to_viewport() -> None:
    for area in _context.screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if region.type == "WINDOW":
                viewport = cast(SidebarRegion, region)
                _context.window.event_simulate(
                    type="MOUSEMOVE",
                    value="NOTHING",
                    x=viewport.x + viewport.width // 2,
                    y=viewport.y + viewport.height // 2,
                )
                return
    raise RuntimeError("No 3D View window region is available")


_production_sidebar_draw = ODM_PT_sidebar.draw


def observed_production_sidebar_draw(self: ODM_PT_sidebar, context: Context) -> None:
    _production_sidebar_draw(self, context)
    scene = context.scene
    assert scene is not None
    runtime = runtime_for_scene(scene)
    observation = (
        str(state.stage),
        runtime.phase,
        runtime.current_frame,
        runtime.completed_work,
        runtime.total_work,
        runtime.phase_completed_work,
        runtime.phase_total_work,
        round(float(runtime.progress), 6),
    )
    draws = state.sidebar_draws
    if not draws or draws[-1] != observation:
        draws.append(observation)
        emit(
            "sidebar_draw",
            stage=observation[0],
            phase=observation[1],
            current_frame=observation[2],
            completed_work=observation[3],
            total_work=observation[4],
            phase_completed_work=observation[5],
            phase_total_work=observation[6],
            progress=observation[7],
        )


def snapshot(stage: str) -> Snapshot:
    scene = _context.scene
    runtime = runtime_for_scene(scene)
    settings = settings_for_scene(scene)
    return {
        "stage": stage,
        "active": runtime.active,
        "cancel_requested": runtime.cancel_requested,
        "phase": runtime.phase,
        "current_frame": runtime.current_frame,
        "completed_work": runtime.completed_work,
        "total_work": runtime.total_work,
        "phase_completed_work": runtime.phase_completed_work,
        "phase_total_work": runtime.phase_total_work,
        "progress": round(float(runtime.progress), 6),
        "runtime_status": runtime.status,
        "settings_status": settings.status,
        "scene_frame": scene.frame_current,
    }


def record_snapshot() -> Snapshot:
    stage = str(state.stage)
    item = snapshot(stage)
    key = tuple(item.values())
    seen = state.seen
    assert isinstance(seen, set)
    if key not in seen:
        seen.add(key)
        snapshots = state.snapshots
        assert isinstance(snapshots, list)
        snapshots.append(item)
        emit("runtime", **item)
    return item


def render_pre(*_args: object) -> None:
    state.render_active = True
    stage = str(state.stage)
    frame = _context.scene.frame_current
    emit("render_pre", stage=stage, frame=frame)
    if stage == "raw_escape_cancel":
        emit("raw_render_active", stage=stage, frame=frame)


def render_complete(*_args: object) -> None:
    state.render_active = False
    emit("render_complete", stage=state.stage, frame=_context.scene.frame_current)


def render_cancel(*_args: object) -> None:
    state.render_active = False
    emit("render_cancel", stage=state.stage, frame=_context.scene.frame_current)


def heartbeat() -> float:
    state.heartbeat_count = int(state.heartbeat_count) + 1
    if state.render_active:
        state.heartbeats_during_render = int(state.heartbeats_during_render) + 1
    return 0.01


def set_output(name: str, *, end: int = 10) -> None:
    settings = settings_for_scene(_context.scene)
    settings.output_directory = str(ROOT / name)
    settings.frame_start = 1
    settings.frame_end = end
    settings.overwrite_raw = False
    settings.overwrite_processed = False
    settings.sequence_run_mode = "REPROCESS"


def all_frame_files(root: Path, *, processed: bool) -> bool:
    paths = SequencePaths(root)
    for number in range(1, 11):
        frame = paths.frame(number)
        required = [frame.beauty, frame.vector, frame.matte]
        if processed:
            required.append(frame.processed)
        if not all(path.is_file() for path in required):
            return False
    return True


def assert_controller_cleared() -> None:
    assert "ODM_active_modal_controller" not in _app.driver_namespace


def event_recorded(event_name: str, marker: str) -> bool:
    if not LOG.exists():
        return False
    return any(
        event.get("event") == event_name and event.get("marker") == marker
        for event in (json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines())
    )


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_output(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def assert_raw_escape_sent_during_render() -> None:
    events = [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines()]
    intervals: list[tuple[float, float]] = []
    for render_pre in events:
        if render_pre["event"] != "raw_render_active":
            continue
        render_complete = next(
            event
            for event in events
            if event["event"] == "render_complete"
            and event["stage"] == "raw_escape_cancel"
            and event["frame"] == render_pre["frame"]
        )
        intervals.append((render_pre["time"], render_complete["time"]))
    escape_times = [
        event["time"]
        for event in events
        if event["event"] in {"external_escape_send_started", "external_escape_sent"}
        and event["marker"] == "raw_render_active"
    ]
    assert len(escape_times) == 2
    escape_start, escape_sent = escape_times
    assert any(
        render_start < escape_start < escape_sent < render_end
        for render_start, render_end in intervals
    )


def start_combined(name: str, *, end: int = 10) -> None:
    set_output(name, end=end)
    result = _odm_ops.render_and_process("INVOKE_DEFAULT")
    emit(
        "operator_invoked",
        stage=state.stage,
        operator="render_and_process",
        result=sorted(result),
    )
    assert result == {"RUNNING_MODAL"}


def start_existing(name: str, *, mode: str = "REPROCESS") -> None:
    set_output(name)
    settings_for_scene(_context.scene).sequence_run_mode = mode
    result = _odm_ops.process_sequence("INVOKE_DEFAULT")
    emit(
        "operator_invoked",
        stage=state.stage,
        operator="process_sequence",
        result=sorted(result),
        mode=mode,
    )
    assert result == {"RUNNING_MODAL"}


def write_result(payload: Mapping[str, object]) -> None:
    temporary = RESULT.with_suffix(f"{RESULT.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(RESULT)


def finish_success() -> None:
    evidence = state.evidence
    assert isinstance(evidence, dict)
    build_hash = _app.build_hash
    if isinstance(build_hash, bytes):
        build_hash = build_hash.decode("ascii")
    assert not git_output(
        "status", "--porcelain", "--untracked-files=all", "--", "src/object_datamosh"
    )
    emit("probe_complete", success=True)
    event_log_jsonl = LOG.read_text(encoding="utf-8")
    trace_sha256 = hashlib.sha256(event_log_jsonl.encode("utf-8")).hexdigest()
    result = {
        "blender_build_hash": build_hash,
        "blender_version": _app.version_string,
        "event_log_jsonl": event_log_jsonl,
        "event_log_sha256_before_completion": trace_sha256,
        "extension_source_tree": git_output("rev-parse", "HEAD:src/object_datamosh"),
        "git_head": git_output("rev-parse", "HEAD"),
        "probe_sha256": file_sha256(Path(__file__)),
        "runner_sha256": file_sha256(REPO / "scripts" / "run_issue26_foreground_probe.sh"),
        "evidence": evidence,
        "heartbeat_count": state.heartbeat_count,
        "heartbeats_during_render": state.heartbeats_during_render,
        "render_complete_handlers": len(_app.handlers.render_complete),
        "render_cancel_handlers": len(_app.handlers.render_cancel),
        "scene_frame": _context.scene.frame_current,
        "success": True,
    }
    write_result(result)
    _wm_ops.quit_blender()


def fail(error: BaseException) -> None:
    details = {"success": False, "error": repr(error), "traceback": traceback.format_exc()}
    emit("probe_complete", **details)
    details["event_log_jsonl"] = LOG.read_text(encoding="utf-8")
    write_result(details)
    _wm_ops.quit_blender()


def _handle_initialize(item: Snapshot, active: bool) -> None:
    if not state.scene_initialized:
        object_datamosh.register()
        scene = _context.scene
        scene.frame_set(state.original_frame)
        render = cast(Any, scene.render)
        cycles = cast(Any, scene.cycles)
        render.engine = "CYCLES"
        cycles.samples = 1
        render.resolution_x = 32
        render.resolution_y = 24
        render.resolution_percentage = 100
        active_object = _context.active_object
        assert active_object is not None
        settings = settings_for_scene(scene)
        settings.target_object = active_object
        set_output("combined-success")
        assert _odm_ops.setup_object_index() == {"FINISHED"}
        for area in _context.screen.areas:
            if area.type == "VIEW_3D":
                active_space = cast(Any, area.spaces.active)
                active_space.show_region_ui = True
                area.tag_redraw()
        state.scene_initialized = True
        return

    region = visible_sidebar_region()
    if region.active_panel_category != "Object Datamosh":
        if state.category_click_offset >= region.height:
            raise RuntimeError("Could not activate the Object Datamosh sidebar category")
        coordinate = (
            region.x + region.width - 8,
            region.y + region.height - state.category_click_offset,
        )
        state.category_click_offset += 10
        simulate_left_click(*coordinate)
        emit("sidebar_category_click", x=coordinate[0], y=coordinate[1])
        return
    if not any(entry[0] == ProbeStage.INITIALIZE.value for entry in state.sidebar_draws):
        return

    state.baseline_complete_handlers = len(_app.handlers.render_complete)
    state.baseline_cancel_handlers = len(_app.handlers.render_cancel)
    state.transition(ProbeStage.COMBINED_SUCCESS)
    start_combined("combined-success")


def _handle_combined_success(item: Snapshot, active: bool) -> None:
    stage = state.stage.value
    if not active:
        assert item["phase"] == "COMPLETED", item
        assert all_frame_files(ROOT / "combined-success", processed=True)
        assert item["scene_frame"] == state.original_frame
        snapshots = state.snapshots
        assert isinstance(snapshots, list)
        combined = [entry for entry in snapshots if entry["stage"] == stage]
        assert {entry["phase"] for entry in combined} >= {"RENDERING", "PROCESSING", "COMPLETED"}
        rendered_counts = {
            entry["completed_work"] for entry in combined if entry["phase"] == "RENDERING"
        }
        processed_counts = {
            entry["completed_work"] for entry in combined if entry["phase"] == "PROCESSING"
        }
        assert set(range(0, 10)).issubset(rendered_counts), rendered_counts
        assert set(range(10, 20)).issubset(processed_counts), processed_counts
        draws = state.sidebar_draws
        assert isinstance(draws, list)
        combined_draws = [entry for entry in draws if entry[0] == stage]
        render_draws = [entry for entry in combined_draws if entry[1] == "RENDERING"]
        process_draws = [entry for entry in combined_draws if entry[1] == "PROCESSING"]
        draw_render_counts = {entry[3] for entry in render_draws}
        draw_process_counts = {entry[3] for entry in process_draws}
        draw_render_phase_counts = {entry[5] for entry in render_draws}
        draw_process_phase_counts = {entry[5] for entry in process_draws}
        draw_progress = {entry[7] for entry in render_draws + process_draws}
        assert set(range(0, 10)).issubset(draw_render_counts), draw_render_counts
        assert set(range(10, 20)).issubset(draw_process_counts), draw_process_counts
        assert set(range(0, 10)).issubset(draw_render_phase_counts), draw_render_phase_counts
        assert set(range(0, 10)).issubset(draw_process_phase_counts), draw_process_phase_counts
        assert {step / 20 for step in range(20)}.issubset(draw_progress), draw_progress
        evidence = state.evidence
        assert isinstance(evidence, dict)
        evidence["combined_success"] = {
            "render_counts": sorted(rendered_counts),
            "process_counts": sorted(processed_counts),
            "sidebar_render_counts": sorted(draw_render_counts),
            "sidebar_process_counts": sorted(draw_process_counts),
            "sidebar_render_phase_counts": sorted(draw_render_phase_counts),
            "sidebar_process_phase_counts": sorted(draw_process_phase_counts),
            "sidebar_progress": sorted(draw_progress),
            "completed_work": item["completed_work"],
            "production_panel_category": "Object Datamosh",
            "progress": item["progress"],
        }
        emit("combined_success_verified", **evidence["combined_success"])
        state.transition(ProbeStage.RAW_BUTTON_CANCEL)
        state.cancel_sent = False
        state.cancel_click_offset = 50
        start_combined("raw-button-cancel", end=100)


def _handle_raw_button_cancel(item: Snapshot, active: bool) -> None:
    stage = state.stage.value
    if active and item["cancel_requested"] and not state.cancel_sent:
        state.cancel_sent = True
        state.cancel_button_coordinate = state.last_click_coordinate
        assert state.cancel_button_coordinate is not None
        assert item["phase"] == "CANCELLING"
        assert item["runtime_status"] == "Cancel requested; waiting for a safe boundary..."
        emit(
            "cancel_button_clicked",
            x=state.cancel_button_coordinate[0],
            y=state.cancel_button_coordinate[1],
        )
    elif (
        active
        and (not state.cancel_sent)
        and (item["phase"] == "RENDERING")
        and (item["completed_work"] >= 1)
    ):
        region = visible_sidebar_region()
        if state.cancel_click_offset >= region.height - 20:
            raise RuntimeError("Could not click the production Cancel control")
        coordinate = (
            region.x + region.width // 2,
            region.y + region.height - state.cancel_click_offset,
        )
        state.cancel_click_offset += 6
        state.last_click_coordinate = coordinate
        simulate_left_click(*coordinate)
        emit("cancel_button_click_attempt", x=coordinate[0], y=coordinate[1])
    elif not active and state.cancel_sent:
        assert item["phase"] == "CANCELLED", item
        assert any(entry[0] == stage and entry[1] == "CANCELLING" for entry in state.sidebar_draws)
        paths = SequencePaths(ROOT / "raw-button-cancel")
        completed = [n for n in range(1, 101) if paths.frame(n).beauty.is_file()]
        assert completed
        assert completed == list(range(1, len(completed) + 1)), completed
        for number in completed:
            frame = paths.frame(number)
            assert all(path.is_file() for path in (frame.beauty, frame.vector, frame.matte))
        next_frame = paths.frame(len(completed) + 1)
        assert not any(
            path.exists() for path in (next_frame.beauty, next_frame.vector, next_frame.matte)
        )
        assert item["scene_frame"] == state.original_frame
        assert_controller_cleared()
        assert len(_app.handlers.render_complete) == state.baseline_complete_handlers
        assert len(_app.handlers.render_cancel) == state.baseline_cancel_handlers
        evidence = state.evidence
        assert isinstance(evidence, dict)
        evidence["raw_button_cancel"] = {
            "button_coordinate": list(verified_cancel_button_coordinate()),
            "completed_frames": completed,
            "controller_cleared": True,
            "handler_counts_restored": True,
            "pending_state_visible": True,
            "pending_status_verified": True,
            "production_mouse_click": True,
        }
        state.transition(ProbeStage.RAW_ESCAPE_CANCEL)
        state.escape_seen = False
        scene = _context.scene
        cycles = cast(Any, scene.cycles)
        cycles.samples = 64
        scene.render.resolution_x = 1024
        scene.render.resolution_y = 1024
        move_pointer_to_viewport()
        start_combined("raw-escape-cancel", end=100)


def _handle_raw_escape_cancel(item: Snapshot, active: bool) -> None:
    stage = state.stage.value
    if active and bool(item["cancel_requested"]):
        state.escape_seen = True
    elif active:
        if item["phase"] != "RENDERING":
            raise RuntimeError(f"Raw Escape entered unexpected phase: {item!r}")
        if (
            event_recorded("external_escape_sent", "raw_render_active")
            and int(item["completed_work"]) >= 10
        ):
            raise RuntimeError("Blender did not dispatch the raw Escape within 10 frames")
    elif item["phase"] == "CANCELLED":
        draws = state.sidebar_draws
        assert isinstance(draws, list)
        pending_visible = any(entry[0] == stage and entry[1] == "CANCELLING" for entry in draws)
        paths = SequencePaths(ROOT / "raw-escape-cancel")
        completed = [n for n in range(1, 101) if paths.frame(n).beauty.is_file()]
        assert completed
        assert completed == list(range(1, len(completed) + 1)), completed
        for number in completed:
            frame = paths.frame(number)
            assert all(path.is_file() for path in (frame.beauty, frame.vector, frame.matte))
        next_frame = paths.frame(len(completed) + 1)
        assert not any(
            path.exists() for path in (next_frame.beauty, next_frame.vector, next_frame.matte)
        )
        assert item["scene_frame"] == state.original_frame
        assert_raw_escape_sent_during_render()
        assert_controller_cleared()
        assert len(_app.handlers.render_complete) == state.baseline_complete_handlers
        assert len(_app.handlers.render_cancel) == state.baseline_cancel_handlers
        evidence = state.evidence
        assert isinstance(evidence, dict)
        evidence["raw_escape_cancel"] = {
            "completed_frames": completed,
            "controller_cleared": True,
            "handler_counts_restored": True,
            "escape_sent_during_render": True,
            "pending_state_visible": pending_visible,
        }
        emit("escape_verified", completed_frames=completed)
        scene = _context.scene
        cycles = cast(Any, scene.cycles)
        cycles.samples = 1
        scene.render.resolution_x = 32
        scene.render.resolution_y = 24
        process_root = ROOT / "processing-cancel"
        shutil.copytree(ROOT / "combined-success" / "raw", process_root / "raw")
        state.transition(ProbeStage.PROCESSING_CANCEL)
        state.processing_cancel_sent = False
        start_existing("processing-cancel")
    else:
        raise RuntimeError(f"Raw Escape ended unexpectedly: {item!r}")


def _handle_processing_cancel(item: Snapshot, active: bool) -> None:
    stage = state.stage.value
    if active and item["cancel_requested"] and state.processing_click_injected:
        state.processing_cancel_sent = True
        assert item["phase"] == "CANCELLING"
        assert item["runtime_status"] == "Cancel requested; waiting for a safe boundary..."
    elif active and (not state.processing_click_injected) and (item["completed_work"] >= 2):
        coordinate = state.cancel_button_coordinate
        assert coordinate is not None
        simulate_left_click(*coordinate)
        state.processing_click_injected = True
        emit("processing_cancel_button_clicked", x=coordinate[0], y=coordinate[1])
    elif not active and state.processing_cancel_sent:
        assert item["phase"] == "CANCELLED", item
        draws = state.sidebar_draws
        assert any(entry[0] == stage and entry[1] == "CANCELLING" for entry in draws)
        paths = SequencePaths(ROOT / "processing-cancel")
        completed = [n for n in range(1, 11) if paths.frame(n).processed.is_file()]
        assert completed
        assert completed == list(range(1, len(completed) + 1)), completed
        assert len(completed) < 10, completed
        assert not paths.frame(len(completed) + 1).processed.exists()
        manifest = paths.root / "processed" / "ODM_sequence_manifest.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        assert payload["completed_frames"] == completed
        assert item["scene_frame"] == state.original_frame
        assert_controller_cleared()
        evidence = state.evidence
        assert isinstance(evidence, dict)
        evidence["processing_button_cancel"] = {
            "button_coordinate": list(verified_cancel_button_coordinate()),
            "completed_frames": completed,
            "controller_cleared": True,
            "pending_state_visible": True,
            "pending_status_verified": True,
            "production_mouse_click": True,
        }
        state.transition(ProbeStage.PROCESSING_RESUME)
        start_existing("processing-cancel", mode="RESUME")


def _handle_processing_resume(item: Snapshot, active: bool) -> None:
    if not active:
        assert item["phase"] == "COMPLETED", item
        assert all_frame_files(ROOT / "processing-cancel", processed=True)
        assert item["scene_frame"] == state.original_frame
        assert_controller_cleared()
        assert len(_app.handlers.render_complete) == state.baseline_complete_handlers
        assert len(_app.handlers.render_cancel) == state.baseline_cancel_handlers
        process_root = ROOT / "processing-escape"
        shutil.copytree(ROOT / "combined-success" / "raw", process_root / "raw")
        state.transition(ProbeStage.PROCESSING_ESCAPE_CANCEL)
        move_pointer_to_viewport()
        start_existing("processing-escape")
        emit("processing_escape_ready")


def _handle_processing_escape_cancel(item: Snapshot, active: bool) -> None:
    stage = state.stage.value
    if active and bool(item["cancel_requested"]):
        assert item["phase"] == "CANCELLING"
        assert item["runtime_status"] == "Cancel requested; waiting for a safe boundary..."
        assert event_recorded("external_escape_send_started", "processing_escape_ready")
        state.processing_escape_seen = True
    elif active:
        if item["phase"] != "PROCESSING":
            raise RuntimeError(f"Processing Escape entered unexpected phase: {item!r}")
        if (
            event_recorded("external_escape_sent", "processing_escape_ready")
            and int(item["completed_work"]) >= 5
        ):
            raise RuntimeError("Blender did not dispatch the processing Escape within five frames")
    elif item["phase"] == "CANCELLED":
        assert state.processing_escape_seen
        draws = state.sidebar_draws
        assert any(entry[0] == stage and entry[1] == "CANCELLING" for entry in draws)
        paths = SequencePaths(ROOT / "processing-escape")
        completed = [n for n in range(1, 11) if paths.frame(n).processed.is_file()]
        assert completed
        assert completed == list(range(1, len(completed) + 1)), completed
        assert len(completed) < 10, completed
        assert not paths.frame(len(completed) + 1).processed.exists()
        manifest = paths.root / "processed" / "ODM_sequence_manifest.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        assert payload["completed_frames"] == completed
        assert item["scene_frame"] == state.original_frame
        assert_controller_cleared()
        evidence = state.evidence
        assert isinstance(evidence, dict)
        evidence["processing_escape_cancel"] = {
            "completed_frames": completed,
            "controller_cleared": True,
            "pending_state_visible": True,
            "pending_status_verified": True,
            "escape_received_by_runtime": True,
        }
        emit("processing_escape_verified", completed_frames=completed)
        state.transition(ProbeStage.PROCESSING_ESCAPE_RESUME)
        start_existing("processing-escape", mode="RESUME")
    else:
        raise RuntimeError(f"Processing Escape ended unexpectedly: {item!r}")


def _handle_processing_escape_resume(item: Snapshot, active: bool) -> None:
    if not active:
        assert item["phase"] == "COMPLETED", item
        assert all_frame_files(ROOT / "processing-escape", processed=True)
        assert item["scene_frame"] == state.original_frame
        assert_controller_cleared()
        evidence = state.evidence
        assert isinstance(evidence, dict)
        evidence["processing_resumes_completed"] = True
        process_root = ROOT / "restart-after-resume"
        shutil.copytree(ROOT / "combined-success" / "raw", process_root / "raw")
        state.transition(ProbeStage.RESTART_AFTER_RESUME)
        state.restart_cancel_sent = False
        start_existing("restart-after-resume")


def _handle_restart_after_resume(item: Snapshot, active: bool) -> None:
    if active and (not state.restart_cancel_sent):
        result = _odm_ops.cancel_operation()
        assert result == {"FINISHED"}
        state.restart_cancel_sent = True
    elif not active and state.restart_cancel_sent:
        assert item["phase"] == "CANCELLED"
        assert_controller_cleared()
        evidence = state.evidence
        assert isinstance(evidence, dict)
        evidence["immediate_restart_completed"] = True
        finish_success()
        return None


_STAGE_HANDLERS: dict[ProbeStage, Callable[[Snapshot, bool], None]] = {
    ProbeStage.INITIALIZE: _handle_initialize,
    ProbeStage.COMBINED_SUCCESS: _handle_combined_success,
    ProbeStage.RAW_BUTTON_CANCEL: _handle_raw_button_cancel,
    ProbeStage.RAW_ESCAPE_CANCEL: _handle_raw_escape_cancel,
    ProbeStage.PROCESSING_CANCEL: _handle_processing_cancel,
    ProbeStage.PROCESSING_RESUME: _handle_processing_resume,
    ProbeStage.PROCESSING_ESCAPE_CANCEL: _handle_processing_escape_cancel,
    ProbeStage.PROCESSING_ESCAPE_RESUME: _handle_processing_escape_resume,
    ProbeStage.RESTART_AFTER_RESUME: _handle_restart_after_resume,
}


def tick() -> float | None:
    try:
        item = record_snapshot()
        active = bool(item["active"])
        _STAGE_HANDLERS[state.stage](item, active)
        return 0.02
    except BaseException as error:
        fail(error)
        return None


cast(Any, ODM_PT_sidebar).draw = observed_production_sidebar_draw
object_datamosh.register()
_app.handlers.render_pre.append(render_pre)
_app.handlers.render_complete.append(render_complete)
_app.handlers.render_cancel.append(render_cancel)
_app.timers.register(heartbeat, first_interval=0.01, persistent=False)
_app.timers.register(tick, first_interval=1.0, persistent=False)
emit("probe_started")
