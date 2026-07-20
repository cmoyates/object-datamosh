"""Presentation-only layout for the Object Datamosh sidebar."""

from __future__ import annotations

from typing import Any


def draw_sidebar(
    layout: Any,
    context: Any,
    settings: Any,
    runtime: Any,
    paths: Any,
    *,
    operation_active: bool,
    phase_label: str,
) -> None:
    """Draw sidebar controls from already-resolved Blender integration state."""
    operation = layout.box()
    operation.label(text=f"Operation: {'Active' if operation_active else 'Idle'}")
    operation.label(text=f"Phase: {phase_label}")
    operation.label(text=f"Frame Range: {runtime.frame_start}-{runtime.frame_end}")
    operation.label(text=f"Current Frame: {runtime.current_frame}")
    operation.label(text=f"Phase Work: {runtime.phase_completed_work}/{runtime.phase_total_work}")
    operation.label(text=f"Overall Work: {runtime.completed_work}/{runtime.total_work}")
    operation.label(text=f"Progress: {runtime.progress:.0%}")
    operation.label(text=f"Status: {runtime.status}")
    if operation_active:
        operation.operator("object_datamosh.cancel_operation")

    target = layout.box()
    target.enabled = not operation_active
    target.label(text="Target")
    target.prop(settings, "target_object")
    target.operator("object_datamosh.use_active_object")
    view_layer_name = context.view_layer.name if context.view_layer is not None else "None"
    target.label(text=f"View Layer: {view_layer_name}")

    sequence = layout.box()
    sequence.enabled = not operation_active
    sequence.label(text="Sequence")
    row = sequence.row(align=True)
    row.prop(settings, "frame_start")
    row.prop(settings, "frame_end")
    sequence.prop(settings, "output_directory")
    sequence.prop(settings, "overwrite_raw")
    active_summary = getattr(runtime, "configuration_summary", "") if operation_active else ""
    if not active_summary:
        history = "Full Frame" if settings.history_source == "FULL_FRAME" else "Target Only"
        mode = "Trail" if settings.feedback_mode == "TRAIL" else "Hard Localized"
        active_summary = (
            f"{history} / {mode} | Persistence {settings.persistence:g} | "
            f"Block {settings.block_size} | Diffusion {settings.diffusion:g} | "
            f"Refresh {settings.refresh_probability:g}"
        )
    sequence.label(text=f"Active: {active_summary}")
    sequence.operator("object_datamosh.render_raw_passes")
    sequence.operator("object_datamosh.render_and_process")
    sequence.prop(settings, "sequence_run_mode")
    sequence.prop(settings, "reset_frames")
    sequence.prop(settings, "resolution_change")
    if settings.sequence_run_mode == "RESUME":
        sequence.prop(settings, "missing_history")
    else:
        sequence.prop(settings, "overwrite_processed")
    sequence.operator("object_datamosh.process_sequence")
    sequence.label(text=f"Output: {paths.root}")
    if paths.warning:
        warning = sequence.row()
        warning.alert = True
        warning.label(text=paths.warning, icon="ERROR")

    matte = layout.box()
    matte.enabled = not operation_active
    matte.label(text="Matte")
    matte.prop(settings, "matte_source")
    if settings.matte_source == "EXTERNAL":
        matte.prop(settings, "external_matte_directory")
    elif settings.matte_source == "CRYPTOMATTE":
        matte.label(text="Experimental; decoding is not yet available", icon="INFO")
    else:
        row = matte.row(align=True)
        row.operator("object_datamosh.setup_object_index")
        row.operator("object_datamosh.restore_object_index")

    calibration = layout.box()
    calibration.enabled = not operation_active
    calibration.label(text="Vector Calibration")
    calibration.operator("object_datamosh.create_vector_calibration")

    feedback = layout.box()
    feedback.enabled = not operation_active
    feedback.label(text="Feedback")
    feedback.prop(settings, "feedback_mode")
    feedback.prop(settings, "history_source")
    if settings.history_source == "TARGET_ONLY":
        warning = feedback.row()
        warning.alert = True
        warning.label(text="Full-frame history is OFF.", icon="INFO")
        warning.label(text="Background and unrelated screen content cannot become history color")
        warning.label(text="inside the target.")
    else:
        feedback.label(
            text="The complete previous processed frame is available as history color.",
            icon="INFO",
        )
    feedback.label(text="First/reset frame:")
    feedback.label(text="Visible object seeds its clean image.")
    feedback.label(text="Background-only pre-roll:")
    feedback.label(text="Enables a more corrupted entrance.")
    feedback.operator("object_datamosh.extreme_full_frame_feedback")
    feedback.label(text="Artistic starting point; results vary by scene.")
    feedback.prop(settings, "trail_decay")
    feedback.prop(settings, "persistence")
    feedback.prop(settings, "block_size")
    feedback.prop(settings, "motion_channels")
    feedback.prop(settings, "reverse_motion")
    axis = feedback.row(align=True)
    axis.prop(settings, "flip_x")
    axis.prop(settings, "flip_y")
    feedback.prop(settings, "motion_gain")
    feedback.prop(settings, "motion_clamp")
    feedback.prop(settings, "motion_quantization")
    feedback.prop(settings, "diffusion")
    feedback.prop(settings, "refresh_probability")
    feedback.prop(settings, "seed")

    if not operation_active:
        layout.label(text=f"Status: {settings.status}")
