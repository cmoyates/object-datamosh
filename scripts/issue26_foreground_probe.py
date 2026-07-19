from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import object_datamosh  # noqa: E402
from object_datamosh.core.paths import SequencePaths  # noqa: E402
from object_datamosh.ui import runtime_for_scene, settings_for_scene  # noqa: E402

WORK_ROOT = Path(tempfile.gettempdir()) / "object-datamosh-issue26"
ROOT = WORK_ROOT / "output"
LOG = WORK_ROOT / "events.jsonl"
RESULT = REPO / "docs" / "evidence" / "issue-26-foreground-result.json"
WORK_ROOT.mkdir(parents=True, exist_ok=True)
shutil.rmtree(ROOT, ignore_errors=True)
LOG.unlink(missing_ok=True)
RESULT.unlink(missing_ok=True)


def emit(event: str, **values: object) -> None:
    record = {"time": round(time.monotonic(), 6), "event": event, **values}
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
        stream.flush()


state: dict[str, object] = {
    "stage": "initialize",
    "snapshots": [],
    "seen": set(),
    "original_frame": 7,
    "render_active": False,
    "heartbeat_count": 0,
    "heartbeats_during_render": 0,
    "render_cancel_requested": False,
    "sidebar_draws": [],
    "evidence": {},
}


class ODM_PT_issue26_observer(bpy.types.Panel):
    bl_idname = "ODM_PT_issue26_observer"
    bl_label = "Issue 26 Draw Observer"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Item"

    def draw(self, context: object) -> None:
        scene = context.scene
        runtime = runtime_for_scene(scene)
        observation = (
            str(state["stage"]),
            runtime.phase,
            runtime.current_frame,
            runtime.completed_work,
            runtime.total_work,
        )
        draws = state["sidebar_draws"]
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
            )
        self.layout.label(text=f"Observed {runtime.completed_work}/{runtime.total_work}")


def snapshot(stage: str) -> dict[str, object]:
    scene = bpy.context.scene
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


def record_snapshot() -> dict[str, object]:
    stage = str(state["stage"])
    item = snapshot(stage)
    key = tuple(item.values())
    seen = state["seen"]
    assert isinstance(seen, set)
    if key not in seen:
        seen.add(key)
        snapshots = state["snapshots"]
        assert isinstance(snapshots, list)
        snapshots.append(item)
        emit("runtime", **item)
    return item


def render_pre(*_args: object) -> None:
    state["render_active"] = True
    emit("render_pre", stage=state["stage"], frame=bpy.context.scene.frame_current)


def render_complete(*_args: object) -> None:
    state["render_active"] = False
    emit("render_complete", stage=state["stage"], frame=bpy.context.scene.frame_current)


def render_cancel(*_args: object) -> None:
    state["render_active"] = False
    emit("render_cancel", stage=state["stage"], frame=bpy.context.scene.frame_current)


def heartbeat() -> float:
    state["heartbeat_count"] = int(state["heartbeat_count"]) + 1
    if state["render_active"]:
        state["heartbeats_during_render"] = int(state["heartbeats_during_render"]) + 1
    return 0.01


def set_output(name: str, *, end: int = 10) -> None:
    settings = settings_for_scene(bpy.context.scene)
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
    assert "ODM_active_modal_controller" not in bpy.app.driver_namespace


def start_combined(name: str, *, end: int = 10) -> None:
    set_output(name, end=end)
    result = bpy.ops.object_datamosh.render_and_process("INVOKE_DEFAULT")
    emit(
        "operator_invoked",
        stage=state["stage"],
        operator="render_and_process",
        result=sorted(result),
    )
    assert result == {"RUNNING_MODAL"}


def start_existing(name: str, *, mode: str = "REPROCESS") -> None:
    set_output(name)
    settings_for_scene(bpy.context.scene).sequence_run_mode = mode
    result = bpy.ops.object_datamosh.process_sequence("INVOKE_DEFAULT")
    emit(
        "operator_invoked",
        stage=state["stage"],
        operator="process_sequence",
        result=sorted(result),
        mode=mode,
    )
    assert result == {"RUNNING_MODAL"}


def finish_success() -> None:
    evidence = state["evidence"]
    assert isinstance(evidence, dict)
    result = {
        "blender_version": bpy.app.version_string,
        "evidence": evidence,
        "heartbeat_count": state["heartbeat_count"],
        "heartbeats_during_render": state["heartbeats_during_render"],
        "render_complete_handlers": len(bpy.app.handlers.render_complete),
        "render_cancel_handlers": len(bpy.app.handlers.render_cancel),
        "scene_frame": bpy.context.scene.frame_current,
        "success": True,
    }
    RESULT.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    emit("probe_complete", success=True)
    bpy.ops.wm.quit_blender()


def fail(error: BaseException) -> None:
    details = {"success": False, "error": repr(error), "traceback": traceback.format_exc()}
    RESULT.write_text(json.dumps(details, indent=2), encoding="utf-8")
    emit("probe_complete", **details)
    bpy.ops.wm.quit_blender()


def tick() -> float | None:
    try:
        stage = str(state["stage"])
        item = record_snapshot()
        active = bool(item["active"])

        if stage == "initialize":
            object_datamosh.register()
            scene = bpy.context.scene
            scene.frame_set(int(state["original_frame"]))
            render = scene.render
            render.engine = "CYCLES"
            scene.cycles.samples = 1
            render.resolution_x = 32
            render.resolution_y = 24
            render.resolution_percentage = 100
            active_object = bpy.context.active_object
            assert active_object is not None
            settings = settings_for_scene(scene)
            settings.target_object = active_object
            set_output("combined-success")
            assert bpy.ops.object_datamosh.setup_object_index() == {"FINISHED"}
            for area in bpy.context.screen.areas:
                if area.type != "VIEW_3D":
                    continue
                area.spaces.active.show_region_ui = True
                area.tag_redraw()
            state["baseline_complete_handlers"] = len(bpy.app.handlers.render_complete)
            state["baseline_cancel_handlers"] = len(bpy.app.handlers.render_cancel)
            state["stage"] = "combined_success"
            start_combined("combined-success")

        elif stage == "combined_success" and not active:
            assert item["phase"] == "COMPLETED", item
            assert all_frame_files(ROOT / "combined-success", processed=True)
            assert item["scene_frame"] == state["original_frame"]
            snapshots = state["snapshots"]
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
            draws = state["sidebar_draws"]
            assert isinstance(draws, list)
            combined_draws = [entry for entry in draws if entry[0] == stage]
            draw_render_counts = {entry[3] for entry in combined_draws if entry[1] == "RENDERING"}
            draw_process_counts = {entry[3] for entry in combined_draws if entry[1] == "PROCESSING"}
            assert set(range(0, 10)).issubset(draw_render_counts), draw_render_counts
            assert set(range(10, 20)).issubset(draw_process_counts), draw_process_counts
            evidence = state["evidence"]
            assert isinstance(evidence, dict)
            evidence["combined_success"] = {
                "render_counts": sorted(rendered_counts),
                "process_counts": sorted(processed_counts),
                "sidebar_render_counts": sorted(draw_render_counts),
                "sidebar_process_counts": sorted(draw_process_counts),
                "completed_work": item["completed_work"],
                "progress": item["progress"],
            }
            emit("combined_success_verified", **evidence["combined_success"])
            state["stage"] = "raw_button_cancel"
            state["cancel_sent"] = False
            start_combined("raw-button-cancel")

        elif stage == "raw_button_cancel":
            if (
                active
                and not state.get("cancel_sent")
                and item["phase"] == "RENDERING"
                and int(item["completed_work"]) >= 1
            ):
                result = bpy.ops.object_datamosh.cancel_operation()
                state["cancel_sent"] = True
                pending = snapshot(stage)
                emit("cancel_button", result=sorted(result), pending=pending)
                assert result == {"FINISHED"}
                assert pending["phase"] == "CANCELLING"
                assert pending["cancel_requested"]
                assert (
                    pending["runtime_status"] == "Cancel requested; waiting for a safe boundary..."
                )
            elif not active and state.get("cancel_sent"):
                assert item["phase"] == "CANCELLED", item
                paths = SequencePaths(ROOT / "raw-button-cancel")
                first = paths.frame(1)
                assert all(path.is_file() for path in (first.beauty, first.vector, first.matte))
                for number in range(2, 11):
                    frame = paths.frame(number)
                    assert not any(
                        path.exists() for path in (frame.beauty, frame.vector, frame.matte)
                    )
                assert item["scene_frame"] == state["original_frame"]
                assert_controller_cleared()
                assert len(bpy.app.handlers.render_complete) == state["baseline_complete_handlers"]
                assert len(bpy.app.handlers.render_cancel) == state["baseline_cancel_handlers"]
                evidence = state["evidence"]
                assert isinstance(evidence, dict)
                evidence["raw_button_cancel"] = {"completed_frames": [1]}
                state["stage"] = "raw_escape_cancel"
                state["escape_seen"] = False
                start_combined("raw-escape-cancel", end=100)
                emit("escape_ready")

        elif stage == "raw_escape_cancel":
            if active and bool(item["cancel_requested"]):
                state["escape_seen"] = True
            elif not active and item["phase"] == "CANCELLED":
                draws = state["sidebar_draws"]
                assert isinstance(draws, list)
                assert any(entry[0] == stage and entry[1] == "CANCELLING" for entry in draws)
                paths = SequencePaths(ROOT / "raw-escape-cancel")
                completed = [n for n in range(1, 101) if paths.frame(n).beauty.is_file()]
                assert completed
                assert completed == list(range(1, len(completed) + 1)), completed
                assert len(completed) < 100, completed
                assert not paths.frame(100).beauty.exists()
                assert item["scene_frame"] == state["original_frame"]
                evidence = state["evidence"]
                assert isinstance(evidence, dict)
                evidence["raw_escape_cancel"] = {"completed_frames": completed}
                emit("escape_verified", completed_frames=completed)
                process_root = ROOT / "processing-cancel"
                shutil.copytree(ROOT / "combined-success" / "raw", process_root / "raw")
                state["stage"] = "processing_cancel"
                state["processing_cancel_sent"] = False
                start_existing("processing-cancel")

        elif stage == "processing_cancel":
            if (
                active
                and not state.get("processing_cancel_sent")
                and int(item["completed_work"]) >= 2
            ):
                result = bpy.ops.object_datamosh.cancel_operation()
                state["processing_cancel_sent"] = True
                pending = snapshot(stage)
                emit("processing_cancel_button", result=sorted(result), pending=pending)
                assert pending["phase"] == "CANCELLING"
                assert pending["cancel_requested"]
            elif not active and state.get("processing_cancel_sent"):
                assert item["phase"] == "CANCELLED", item
                paths = SequencePaths(ROOT / "processing-cancel")
                completed = [n for n in range(1, 11) if paths.frame(n).processed.is_file()]
                assert completed == list(range(1, len(completed) + 1)), completed
                manifest = paths.root / "processed" / "ODM_sequence_manifest.json"
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                assert payload["completed_frames"] == completed
                assert item["scene_frame"] == state["original_frame"]
                assert_controller_cleared()
                evidence = state["evidence"]
                assert isinstance(evidence, dict)
                evidence["processing_button_cancel"] = {"completed_frames": completed}
                state["stage"] = "processing_resume"
                start_existing("processing-cancel", mode="RESUME")

        elif stage == "processing_resume" and not active:
            assert item["phase"] == "COMPLETED", item
            assert all_frame_files(ROOT / "processing-cancel", processed=True)
            assert item["scene_frame"] == state["original_frame"]
            assert_controller_cleared()
            assert len(bpy.app.handlers.render_complete) == state["baseline_complete_handlers"]
            assert len(bpy.app.handlers.render_cancel) == state["baseline_cancel_handlers"]
            process_root = ROOT / "processing-escape"
            shutil.copytree(ROOT / "combined-success" / "raw", process_root / "raw")
            state["stage"] = "processing_escape_cancel"
            start_existing("processing-escape")
            emit("processing_escape_ready")

        elif stage == "processing_escape_cancel":
            if active and bool(item["cancel_requested"]):
                state["processing_escape_seen"] = True
            elif not active and item["phase"] == "CANCELLED":
                assert state.get("processing_escape_seen")
                paths = SequencePaths(ROOT / "processing-escape")
                completed = [n for n in range(1, 11) if paths.frame(n).processed.is_file()]
                assert completed
                assert completed == list(range(1, len(completed) + 1)), completed
                assert len(completed) < 10, completed
                manifest = paths.root / "processed" / "ODM_sequence_manifest.json"
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                assert payload["completed_frames"] == completed
                assert item["scene_frame"] == state["original_frame"]
                assert_controller_cleared()
                evidence = state["evidence"]
                assert isinstance(evidence, dict)
                evidence["processing_escape_cancel"] = {"completed_frames": completed}
                emit("processing_escape_verified", completed_frames=completed)
                state["stage"] = "processing_escape_resume"
                start_existing("processing-escape", mode="RESUME")

        elif stage == "processing_escape_resume" and not active:
            assert item["phase"] == "COMPLETED", item
            assert all_frame_files(ROOT / "processing-escape", processed=True)
            assert item["scene_frame"] == state["original_frame"]
            assert_controller_cleared()
            evidence = state["evidence"]
            assert isinstance(evidence, dict)
            evidence["processing_resumes_completed"] = True
            # A completed Resume can immediately be followed by another operation.
            process_root = ROOT / "restart-after-resume"
            shutil.copytree(ROOT / "combined-success" / "raw", process_root / "raw")
            state["stage"] = "restart_after_resume"
            state["restart_cancel_sent"] = False
            start_existing("restart-after-resume")

        elif stage == "restart_after_resume":
            if active and not state.get("restart_cancel_sent"):
                result = bpy.ops.object_datamosh.cancel_operation()
                assert result == {"FINISHED"}
                state["restart_cancel_sent"] = True
            elif not active and state.get("restart_cancel_sent"):
                assert item["phase"] == "CANCELLED"
                assert_controller_cleared()
                evidence = state["evidence"]
                assert isinstance(evidence, dict)
                evidence["immediate_restart_completed"] = True
                finish_success()
                return None

        return 0.02
    except BaseException as error:
        fail(error)
        return None


object_datamosh.register()
bpy.utils.register_class(ODM_PT_issue26_observer)
bpy.app.handlers.render_pre.append(render_pre)
bpy.app.handlers.render_complete.append(render_complete)
bpy.app.handlers.render_cancel.append(render_cancel)
bpy.app.timers.register(heartbeat, first_interval=0.01, persistent=False)
bpy.app.timers.register(tick, first_interval=1.0, persistent=False)
emit("probe_started")
