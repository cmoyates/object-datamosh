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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import bpy

_bpy = cast(Any, bpy)

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


@dataclass
class ProbeState:
    stage: str = "initialize"
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    seen: set[tuple[Any, ...]] = field(default_factory=set)
    original_frame: int = 7
    render_active: bool = False
    heartbeat_count: int = 0
    heartbeats_during_render: int = 0
    sidebar_draws: list[tuple[Any, ...]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    baseline_complete_handlers: int = 0
    baseline_cancel_handlers: int = 0
    cancel_sent: bool = False
    escape_seen: bool = False
    processing_cancel_sent: bool = False
    processing_escape_seen: bool = False
    restart_cancel_sent: bool = False


state = ProbeState()


class ODM_PT_issue26_observer(_bpy.types.Panel):
    bl_idname = "ODM_PT_issue26_observer"
    bl_label = "Issue 26 Draw Observer"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Item"

    def draw(self, context: Any) -> None:
        # Render the production sidebar layout in the active Item tab so this probe observes a
        # genuinely visible panel without relying on Blender's non-public category-selection state.
        cast(Any, ODM_PT_sidebar).draw(self, context)
        scene = context.scene
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
        assert isinstance(draws, list)
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
        layout = self.layout
        assert layout is not None
        layout.label(text=f"Observed {runtime.completed_work}/{runtime.total_work}")


def snapshot(stage: str) -> dict[str, Any]:
    scene = _bpy.context.scene
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


def record_snapshot() -> dict[str, Any]:
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
    frame = _bpy.context.scene.frame_current
    emit("render_pre", stage=stage, frame=frame)
    if stage == "raw_escape_cancel":
        emit("raw_render_active", stage=stage, frame=frame)


def render_complete(*_args: object) -> None:
    state.render_active = False
    emit("render_complete", stage=state.stage, frame=_bpy.context.scene.frame_current)


def render_cancel(*_args: object) -> None:
    state.render_active = False
    emit("render_cancel", stage=state.stage, frame=_bpy.context.scene.frame_current)


def heartbeat() -> float:
    state.heartbeat_count = int(state.heartbeat_count) + 1
    if state.render_active:
        state.heartbeats_during_render = int(state.heartbeats_during_render) + 1
    return 0.01


def set_output(name: str, *, end: int = 10) -> None:
    settings = settings_for_scene(_bpy.context.scene)
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
    assert "ODM_active_modal_controller" not in _bpy.app.driver_namespace


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
    assert all(any(start < escape < end for start, end in intervals) for escape in escape_times)


def start_combined(name: str, *, end: int = 10) -> None:
    set_output(name, end=end)
    result = _bpy.ops.object_datamosh.render_and_process("INVOKE_DEFAULT")
    emit(
        "operator_invoked",
        stage=state.stage,
        operator="render_and_process",
        result=sorted(result),
    )
    assert result == {"RUNNING_MODAL"}


def start_existing(name: str, *, mode: str = "REPROCESS") -> None:
    set_output(name)
    settings_for_scene(_bpy.context.scene).sequence_run_mode = mode
    result = _bpy.ops.object_datamosh.process_sequence("INVOKE_DEFAULT")
    emit(
        "operator_invoked",
        stage=state.stage,
        operator="process_sequence",
        result=sorted(result),
        mode=mode,
    )
    assert result == {"RUNNING_MODAL"}


def finish_success() -> None:
    evidence = state.evidence
    assert isinstance(evidence, dict)
    build_hash = _bpy.app.build_hash
    if isinstance(build_hash, bytes):
        build_hash = build_hash.decode("ascii")
    assert not git_output(
        "status", "--porcelain", "--untracked-files=all", "--", "src/object_datamosh"
    )
    result = {
        "blender_build_hash": build_hash,
        "blender_version": _bpy.app.version_string,
        "event_log_sha256_before_completion": file_sha256(LOG),
        "extension_source_tree": git_output("rev-parse", "HEAD:src/object_datamosh"),
        "git_head": git_output("rev-parse", "HEAD"),
        "probe_sha256": file_sha256(Path(__file__)),
        "runner_sha256": file_sha256(REPO / "scripts" / "run_issue26_foreground_probe.sh"),
        "evidence": evidence,
        "heartbeat_count": state.heartbeat_count,
        "heartbeats_during_render": state.heartbeats_during_render,
        "render_complete_handlers": len(_bpy.app.handlers.render_complete),
        "render_cancel_handlers": len(_bpy.app.handlers.render_cancel),
        "scene_frame": _bpy.context.scene.frame_current,
        "success": True,
    }
    RESULT.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    emit("probe_complete", success=True)
    _bpy.ops.wm.quit_blender()


def fail(error: BaseException) -> None:
    details = {"success": False, "error": repr(error), "traceback": traceback.format_exc()}
    RESULT.write_text(json.dumps(details, indent=2), encoding="utf-8")
    emit("probe_complete", **details)
    _bpy.ops.wm.quit_blender()


def tick() -> float | None:
    try:
        stage = str(state.stage)
        item = record_snapshot()
        active = bool(item["active"])

        if stage == "initialize":
            object_datamosh.register()
            scene = _bpy.context.scene
            scene.frame_set(int(state.original_frame))
            render = scene.render
            render.engine = "CYCLES"
            scene.cycles.samples = 1
            render.resolution_x = 32
            render.resolution_y = 24
            render.resolution_percentage = 100
            active_object = _bpy.context.active_object
            assert active_object is not None
            settings = settings_for_scene(scene)
            settings.target_object = active_object
            set_output("combined-success")
            assert _bpy.ops.object_datamosh.setup_object_index() == {"FINISHED"}
            for area in _bpy.context.screen.areas:
                if area.type != "VIEW_3D":
                    continue
                area.spaces.active.show_region_ui = True
                area.tag_redraw()
            state.baseline_complete_handlers = len(_bpy.app.handlers.render_complete)
            state.baseline_cancel_handlers = len(_bpy.app.handlers.render_cancel)
            state.stage = "combined_success"
            start_combined("combined-success")

        elif stage == "combined_success" and not active:
            assert item["phase"] == "COMPLETED", item
            assert all_frame_files(ROOT / "combined-success", processed=True)
            assert item["scene_frame"] == state.original_frame
            snapshots = state.snapshots
            assert isinstance(snapshots, list)
            combined = [entry for entry in snapshots if entry["stage"] == stage]
            assert {entry["phase"] for entry in combined} >= {
                "RENDERING",
                "PROCESSING",
                "COMPLETED",
            }
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
                "progress": item["progress"],
            }
            emit("combined_success_verified", **evidence["combined_success"])
            state.stage = "raw_button_cancel"
            state.cancel_sent = False
            start_combined("raw-button-cancel")

        elif stage == "raw_button_cancel":
            if (
                active
                and not state.cancel_sent
                and item["phase"] == "RENDERING"
                and int(item["completed_work"]) >= 1
            ):
                result = _bpy.ops.object_datamosh.cancel_operation()
                state.cancel_sent = True
                pending = snapshot(stage)
                emit("cancel_button", result=sorted(result), pending=pending)
                assert result == {"FINISHED"}
                assert pending["phase"] == "CANCELLING"
                assert pending["cancel_requested"]
                assert (
                    pending["runtime_status"] == "Cancel requested; waiting for a safe boundary..."
                )
            elif not active and state.cancel_sent:
                assert item["phase"] == "CANCELLED", item
                paths = SequencePaths(ROOT / "raw-button-cancel")
                first = paths.frame(1)
                assert all(path.is_file() for path in (first.beauty, first.vector, first.matte))
                for number in range(2, 11):
                    frame = paths.frame(number)
                    assert not any(
                        path.exists() for path in (frame.beauty, frame.vector, frame.matte)
                    )
                assert item["scene_frame"] == state.original_frame
                assert_controller_cleared()
                assert len(_bpy.app.handlers.render_complete) == state.baseline_complete_handlers
                assert len(_bpy.app.handlers.render_cancel) == state.baseline_cancel_handlers
                evidence = state.evidence
                assert isinstance(evidence, dict)
                evidence["raw_button_cancel"] = {
                    "completed_frames": [1],
                    "controller_cleared": True,
                    "handler_counts_restored": True,
                }
                state.stage = "raw_escape_cancel"
                state.escape_seen = False
                scene = _bpy.context.scene
                scene.cycles.samples = 16
                scene.render.resolution_x = 512
                scene.render.resolution_y = 512
                start_combined("raw-escape-cancel", end=100)

        elif stage == "raw_escape_cancel":
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
                pending_visible = any(
                    entry[0] == stage and entry[1] == "CANCELLING" for entry in draws
                )
                paths = SequencePaths(ROOT / "raw-escape-cancel")
                completed = [n for n in range(1, 101) if paths.frame(n).beauty.is_file()]
                assert completed
                assert completed == list(range(1, len(completed) + 1)), completed
                for number in completed:
                    frame = paths.frame(number)
                    assert all(path.is_file() for path in (frame.beauty, frame.vector, frame.matte))
                next_frame = paths.frame(len(completed) + 1)
                assert not any(
                    path.exists()
                    for path in (next_frame.beauty, next_frame.vector, next_frame.matte)
                )
                assert item["scene_frame"] == state.original_frame
                assert_raw_escape_sent_during_render()
                assert_controller_cleared()
                assert len(_bpy.app.handlers.render_complete) == state.baseline_complete_handlers
                assert len(_bpy.app.handlers.render_cancel) == state.baseline_cancel_handlers
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
                scene = _bpy.context.scene
                scene.cycles.samples = 1
                scene.render.resolution_x = 32
                scene.render.resolution_y = 24
                process_root = ROOT / "processing-cancel"
                shutil.copytree(ROOT / "combined-success" / "raw", process_root / "raw")
                state.stage = "processing_cancel"
                state.processing_cancel_sent = False
                start_existing("processing-cancel")
            else:
                raise RuntimeError(f"Raw Escape ended unexpectedly: {item!r}")

        elif stage == "processing_cancel":
            if active and not state.processing_cancel_sent and int(item["completed_work"]) >= 2:
                result = _bpy.ops.object_datamosh.cancel_operation()
                state.processing_cancel_sent = True
                pending = snapshot(stage)
                emit("processing_cancel_button", result=sorted(result), pending=pending)
                assert pending["phase"] == "CANCELLING"
                assert pending["cancel_requested"]
                assert (
                    pending["runtime_status"] == "Cancel requested; waiting for a safe boundary..."
                )
            elif not active and state.processing_cancel_sent:
                assert item["phase"] == "CANCELLED", item
                draws = state.sidebar_draws
                assert any(entry[0] == stage and entry[1] == "CANCELLING" for entry in draws)
                paths = SequencePaths(ROOT / "processing-cancel")
                completed = [n for n in range(1, 11) if paths.frame(n).processed.is_file()]
                assert completed == [1, 2], completed
                assert not paths.frame(3).processed.exists()
                manifest = paths.root / "processed" / "ODM_sequence_manifest.json"
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                assert payload["completed_frames"] == completed
                assert item["scene_frame"] == state.original_frame
                assert_controller_cleared()
                evidence = state.evidence
                assert isinstance(evidence, dict)
                evidence["processing_button_cancel"] = {
                    "completed_frames": completed,
                    "controller_cleared": True,
                    "pending_state_visible": True,
                    "pending_status_verified": True,
                }
                state.stage = "processing_resume"
                start_existing("processing-cancel", mode="RESUME")

        elif stage == "processing_resume" and not active:
            assert item["phase"] == "COMPLETED", item
            assert all_frame_files(ROOT / "processing-cancel", processed=True)
            assert item["scene_frame"] == state.original_frame
            assert_controller_cleared()
            assert len(_bpy.app.handlers.render_complete) == state.baseline_complete_handlers
            assert len(_bpy.app.handlers.render_cancel) == state.baseline_cancel_handlers
            process_root = ROOT / "processing-escape"
            shutil.copytree(ROOT / "combined-success" / "raw", process_root / "raw")
            state.stage = "processing_escape_cancel"
            start_existing("processing-escape")
            emit("processing_escape_ready")

        elif stage == "processing_escape_cancel":
            if active and bool(item["cancel_requested"]):
                assert item["phase"] == "CANCELLING"
                assert item["runtime_status"] == "Cancel requested; waiting for a safe boundary..."
                state.processing_escape_seen = True
            elif active:
                if item["phase"] != "PROCESSING":
                    raise RuntimeError(f"Processing Escape entered unexpected phase: {item!r}")
                if (
                    event_recorded("external_escape_sent", "processing_escape_ready")
                    and int(item["completed_work"]) >= 5
                ):
                    raise RuntimeError(
                        "Blender did not dispatch the processing Escape within five frames"
                    )
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
                }
                emit("processing_escape_verified", completed_frames=completed)
                state.stage = "processing_escape_resume"
                start_existing("processing-escape", mode="RESUME")
            else:
                raise RuntimeError(f"Processing Escape ended unexpectedly: {item!r}")

        elif stage == "processing_escape_resume" and not active:
            assert item["phase"] == "COMPLETED", item
            assert all_frame_files(ROOT / "processing-escape", processed=True)
            assert item["scene_frame"] == state.original_frame
            assert_controller_cleared()
            evidence = state.evidence
            assert isinstance(evidence, dict)
            evidence["processing_resumes_completed"] = True
            # A completed Resume can immediately be followed by another operation.
            process_root = ROOT / "restart-after-resume"
            shutil.copytree(ROOT / "combined-success" / "raw", process_root / "raw")
            state.stage = "restart_after_resume"
            state.restart_cancel_sent = False
            start_existing("restart-after-resume")

        elif stage == "restart_after_resume":
            if active and not state.restart_cancel_sent:
                result = _bpy.ops.object_datamosh.cancel_operation()
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

        known_stages = {
            "initialize",
            "combined_success",
            "raw_button_cancel",
            "raw_escape_cancel",
            "processing_cancel",
            "processing_resume",
            "processing_escape_cancel",
            "processing_escape_resume",
            "restart_after_resume",
        }
        if stage not in known_stages:
            raise RuntimeError(f"Unknown foreground-probe stage: {stage}")
        return 0.02
    except BaseException as error:
        fail(error)
        return None


object_datamosh.register()
_bpy.utils.register_class(ODM_PT_issue26_observer)
_bpy.app.handlers.render_pre.append(render_pre)
_bpy.app.handlers.render_complete.append(render_complete)
_bpy.app.handlers.render_cancel.append(render_cancel)
_bpy.app.timers.register(heartbeat, first_interval=0.01, persistent=False)
_bpy.app.timers.register(tick, first_interval=1.0, persistent=False)
emit("probe_started")
