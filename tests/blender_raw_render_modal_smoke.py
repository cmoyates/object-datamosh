"""Focused deterministic modal raw-render scenarios for the Blender smoke gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import bpy
from blender_modal_test_support import ModalWindowManagerRecorder, ProcessOperatorHarness

from object_datamosh.core.paths import SequencePaths
from object_datamosh.ui import ODM_OT_render_raw_passes


def _context(scene: Any, window_manager: ModalWindowManagerRecorder) -> Any:
    return type(
        "RawModalContext",
        (),
        {
            "scene": scene,
            "view_layer": scene.view_layers[0],
            "window_manager": window_manager,
            "window": object(),
        },
    )()


def _timer(window_manager: ModalWindowManagerRecorder) -> Any:
    return type("RawTimerEvent", (), {"type": "TIMER", "timer": window_manager.timer})()


def run_raw_render_modal_scenarios(
    scene: Any,
    settings: Any,
    runtime: Any,
    object_datamosh_ops: Any,
    root: Path,
) -> None:
    """Verify startup, progress, locking, completion, cancellation, and cleanup in real bpy."""
    settings.output_directory = str(root / "complete")
    settings.frame_start = 1
    settings.frame_end = 1
    settings.overwrite_raw = False
    original_frame = scene.frame_current
    window_manager = ModalWindowManagerRecorder()
    context = _context(scene, window_manager)
    operator = ProcessOperatorHarness(ODM_OT_render_raw_passes)
    complete_handlers_before = len(bpy.app.handlers.render_complete)
    cancel_handlers_before = len(bpy.app.handlers.render_cancel)

    assert operator.execute(context) == {"RUNNING_MODAL"}
    assert runtime.active
    assert runtime.phase == "RENDERING"
    assert not object_datamosh_ops.render_raw_passes.poll()
    assert window_manager.events[:4] == [
        ("progress_begin", (0, 1)),
        ("timer_add", (0.1, context.window)),
        ("progress_update", 0),
        ("modal_handler_add", operator),
    ]
    timer = _timer(window_manager)
    launch_result = operator.modal(context, timer)
    assert launch_result == {"RUNNING_MODAL"}, (launch_result, operator.reports, runtime.status)
    assert len(bpy.app.handlers.render_complete) == complete_handlers_before + 1
    assert len(bpy.app.handlers.render_cancel) == cancel_handlers_before + 1
    assert operator.modal(context, timer) == {"FINISHED"}
    assert len(bpy.app.handlers.render_complete) == complete_handlers_before
    assert len(bpy.app.handlers.render_cancel) == cancel_handlers_before
    frame = SequencePaths(root / "complete").frame(1)
    assert frame.beauty.is_file() and frame.vector.is_file() and frame.matte.is_file()
    assert not runtime.active
    assert runtime.phase == "COMPLETED"
    assert runtime.completed_work == 1
    assert runtime.progress == 1.0
    assert scene.frame_current == original_frame
    assert window_manager.events[-2:] == [
        ("timer_remove", window_manager.timer),
        ("progress_end", None),
    ]

    settings.output_directory = str(root / "cancelled")
    settings.frame_start = 1
    settings.frame_end = 2
    cancelled_window_manager = ModalWindowManagerRecorder()
    cancelled_context = _context(scene, cancelled_window_manager)
    cancelled_operator = ProcessOperatorHarness(ODM_OT_render_raw_passes)
    cancelled_timer = _timer(cancelled_window_manager)
    assert cancelled_operator.execute(cancelled_context) == {"RUNNING_MODAL"}
    assert cancelled_operator.modal(cancelled_context, cancelled_timer) == {"RUNNING_MODAL"}
    assert object_datamosh_ops.cancel_operation() == {"FINISHED"}
    assert runtime.cancel_requested
    assert runtime.phase == "CANCELLING"
    assert cancelled_operator.modal(cancelled_context, cancelled_timer) == {"CANCELLED"}
    cancelled_paths = SequencePaths(root / "cancelled")
    assert cancelled_paths.frame(1).beauty.is_file()
    assert not cancelled_paths.frame(2).beauty.exists()
    assert not runtime.active
    assert runtime.phase == "CANCELLED"
    assert scene.frame_current == original_frame
    assert cancelled_window_manager.events[-2:] == [
        ("timer_remove", cancelled_window_manager.timer),
        ("progress_end", None),
    ]
