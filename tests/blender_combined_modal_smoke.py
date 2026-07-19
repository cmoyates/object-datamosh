"""Focused modal Render and Process scenario for the Blender smoke gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from blender_modal_test_support import ModalWindowManagerRecorder, ProcessOperatorHarness

from object_datamosh.core.paths import SequencePaths
from object_datamosh.ui import ODM_OT_render_and_process


def run_combined_modal_scenario(
    scene: Any,
    settings: Any,
    runtime: Any,
    object_datamosh_ops: Any,
    root: Path,
) -> None:
    """Verify modal startup, phase transition, locking, completion, and cleanup."""
    settings.output_directory = str(root)
    settings.frame_start = 1
    settings.frame_end = 2
    settings.overwrite_raw = False
    settings.overwrite_processed = False
    original_frame = scene.frame_current
    window_manager = ModalWindowManagerRecorder()
    context = type(
        "CombinedModalContext",
        (),
        {
            "scene": scene,
            "view_layer": scene.view_layers[0],
            "window_manager": window_manager,
            "window": object(),
        },
    )()
    operator = ProcessOperatorHarness(ODM_OT_render_and_process)
    timer = type(
        "CombinedTimerEvent",
        (),
        {"type": "TIMER", "timer": window_manager.timer},
    )()

    assert operator.execute(context) == {"RUNNING_MODAL"}
    assert runtime.active
    assert runtime.phase == "RENDERING"
    assert runtime.total_work == 4
    assert not object_datamosh_ops.render_and_process.poll()
    assert window_manager.events[:4] == [
        ("progress_begin", (0, 4)),
        ("timer_add", (0.1, context.window)),
        ("progress_update", 0),
        ("modal_handler_add", operator),
    ]

    assert operator.modal(context, timer) == {"RUNNING_MODAL"}
    assert runtime.phase == "RENDERING"
    assert runtime.completed_work == 1
    assert operator.modal(context, timer) == {"RUNNING_MODAL"}
    assert runtime.phase == "PROCESSING"
    assert runtime.completed_work == 2
    assert runtime.progress == 0.5
    assert operator.modal(context, timer) == {"RUNNING_MODAL"}
    assert runtime.phase == "PROCESSING"
    assert runtime.completed_work == 3
    assert operator.modal(context, timer) == {"FINISHED"}

    paths = SequencePaths(root)
    inventory = tuple(
        path
        for frame in (paths.frame(1), paths.frame(2))
        for path in (frame.beauty, frame.vector, frame.matte, frame.processed)
    )
    assert all(path.is_file() for path in inventory), inventory
    assert not runtime.active
    assert runtime.phase == "COMPLETED"
    assert runtime.completed_work == 4
    assert runtime.progress == 1.0
    assert runtime.status == "Render and Process complete: 2 frame(s)"
    assert settings.status == runtime.status
    assert scene.frame_current == original_frame
    assert window_manager.events[-2:] == [
        ("timer_remove", window_manager.timer),
        ("progress_end", None),
    ]
    assert object_datamosh_ops.render_and_process.poll()

    render_cancel_root = root.parent / "combined-render-cancel"
    settings.output_directory = str(render_cancel_root)
    render_cancel_manager = ModalWindowManagerRecorder()
    render_cancel_context = type(
        "CombinedRenderCancelContext",
        (),
        {
            "scene": scene,
            "view_layer": scene.view_layers[0],
            "window_manager": render_cancel_manager,
            "window": object(),
        },
    )()
    render_cancel_operator = ProcessOperatorHarness(ODM_OT_render_and_process)
    render_cancel_timer = type(
        "CombinedRenderCancelTimer",
        (),
        {"type": "TIMER", "timer": render_cancel_manager.timer},
    )()
    assert render_cancel_operator.execute(render_cancel_context) == {"RUNNING_MODAL"}
    assert render_cancel_operator.modal(render_cancel_context, render_cancel_timer) == {
        "RUNNING_MODAL"
    }
    assert object_datamosh_ops.cancel_operation() == {"FINISHED"}
    assert runtime.phase == "CANCELLING"
    assert render_cancel_operator.modal(render_cancel_context, render_cancel_timer) == {
        "CANCELLED"
    }
    render_cancel_paths = SequencePaths(render_cancel_root)
    assert render_cancel_paths.frame(1).beauty.is_file()
    assert not render_cancel_paths.frame(2).beauty.exists()
    assert not runtime.active
    assert runtime.phase == "CANCELLED"
    assert scene.frame_current == original_frame

    process_cancel_root = root.parent / "combined-process-cancel"
    settings.output_directory = str(process_cancel_root)
    process_cancel_manager = ModalWindowManagerRecorder()
    process_cancel_context = type(
        "CombinedProcessCancelContext",
        (),
        {
            "scene": scene,
            "view_layer": scene.view_layers[0],
            "window_manager": process_cancel_manager,
            "window": object(),
        },
    )()
    process_cancel_operator = ProcessOperatorHarness(ODM_OT_render_and_process)
    process_cancel_timer = type(
        "CombinedProcessCancelTimer",
        (),
        {"type": "TIMER", "timer": process_cancel_manager.timer},
    )()
    assert process_cancel_operator.execute(process_cancel_context) == {"RUNNING_MODAL"}
    assert process_cancel_operator.modal(process_cancel_context, process_cancel_timer) == {
        "RUNNING_MODAL"
    }
    assert process_cancel_operator.modal(process_cancel_context, process_cancel_timer) == {
        "RUNNING_MODAL"
    }
    assert runtime.phase == "PROCESSING"
    assert process_cancel_operator.modal(process_cancel_context, process_cancel_timer) == {
        "RUNNING_MODAL"
    }
    assert object_datamosh_ops.cancel_operation() == {"FINISHED"}
    assert runtime.phase == "CANCELLING"
    assert process_cancel_operator.modal(process_cancel_context, process_cancel_timer) == {
        "CANCELLED"
    }
    process_cancel_paths = SequencePaths(process_cancel_root)
    assert process_cancel_paths.frame(1).processed.is_file()
    assert not process_cancel_paths.frame(2).processed.exists()
    assert process_cancel_paths.frame(2).beauty.is_file()
    assert not runtime.active
    assert runtime.phase == "CANCELLED"
    assert scene.frame_current == original_frame
    assert object_datamosh_ops.render_and_process.poll()
