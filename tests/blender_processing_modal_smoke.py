"""Focused modal existing-pass scenarios for the Blender smoke gate."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import bpy
import numpy as np
from blender_modal_test_support import ModalWindowManagerRecorder, ProcessOperatorHarness

from object_datamosh.blender_image_io import BlenderImageIO
from object_datamosh.core.paths import SequencePaths
from object_datamosh.ui import ODM_OT_process_sequence


def run_processing_modal_scenarios(
    scene: Any,
    settings: Any,
    runtime: Any,
    image_io: BlenderImageIO,
    object_datamosh_ops: Any,
    exr_contract: Callable[[Path], tuple[tuple[int, int], tuple[int, ...]]],
) -> None:
    """Check bounded progress, locks, cancellation, recovery, failures, and restart."""
    with tempfile.TemporaryDirectory(prefix="ODM_processing_smoke_") as temp_directory:
        processing_paths = SequencePaths(Path(temp_directory))
        first = processing_paths.frame(1)
        second = processing_paths.frame(2)
        first_beauty = np.full((2, 3, 4), 0.8, dtype=np.float32)
        second_beauty = np.full((2, 3, 4), 0.1, dtype=np.float32)
        zero_vector = np.zeros((2, 3, 4), dtype=np.float32)
        selected = np.zeros((2, 3), dtype=np.float32)
        selected[:, 1] = 1.0
        matte_rgba = np.repeat(selected[..., None], 4, axis=2)
        processing_images_before = len(bpy.data.images)
        for frame_paths, beauty in ((first, first_beauty), (second, second_beauty)):
            image_io.write_rgba(frame_paths.beauty, beauty)
            image_io.write_rgba(frame_paths.vector, zero_vector)
            image_io.write_rgba(frame_paths.matte, matte_rgba)

        settings.output_directory = str(processing_paths.root)
        settings.frame_start = 1
        settings.frame_end = 2
        settings.matte_source = "OBJECT_INDEX"
        settings.persistence = 1.0
        settings.block_size = 1
        settings.overwrite_processed = False
        assert object_datamosh_ops.extreme_full_frame_feedback() == {"FINISHED"}
        assert settings.history_source == "FULL_FRAME"
        assert settings.feedback_mode == "TRAIL"
        # Keep the preset's identity while making this tiny image fixture pixel-local.
        settings.block_size = 1
        settings.refresh_probability = 0.0
        settings.motion_quantization = 0.0
        settings.diffusion = 0.0
        modal_window_manager = ModalWindowManagerRecorder()
        modal_window = object()
        modal_context = type(
            "ModalContext",
            (),
            {
                "scene": scene,
                "window_manager": modal_window_manager,
                "window": modal_window,
            },
        )()
        process_operator = ProcessOperatorHarness(ODM_OT_process_sequence)
        assert process_operator.execute(modal_context) == {"RUNNING_MODAL"}
        assert runtime.active
        assert runtime.phase == "PROCESSING"
        assert runtime.current_frame == 1
        assert runtime.completed_work == 0
        assert runtime.total_work == 2
        assert runtime.progress == 0.0
        assert settings.status == "Starting: Full Frame / Trail"
        assert runtime.status == "Processing: Full Frame / Trail (frame 1 of 2)"
        assert process_operator.reports[-1] == ({"INFO"}, "Starting: Full Frame / Trail")
        assert runtime.configuration_summary.startswith("Full Frame / Trail")
        assert runtime.manifest_path == str(
            processing_paths.root / "processed" / "ODM_sequence_manifest.json"
        )
        # Scene controls are mutable, but this active session must retain its invocation snapshot.
        settings.history_source = "TARGET_ONLY"
        settings.feedback_mode = "HARD_LOCALIZED"
        assert modal_window_manager.events[:4] == [
            ("progress_begin", (0, 2)),
            ("timer_add", (0.1, modal_window)),
            ("progress_update", 0),
            ("modal_handler_add", process_operator),
        ]
        foreign_timer_event = type(
            "ForeignTimerEvent",
            (),
            {"type": "TIMER", "timer": object()},
        )()
        assert process_operator.modal(modal_context, foreign_timer_event) == {"PASS_THROUGH"}
        assert not first.processed.exists()
        timer_event = type(
            "TimerEvent",
            (),
            {"type": "TIMER", "timer": modal_window_manager.timer},
        )()
        assert process_operator.modal(modal_context, timer_event) == {"RUNNING_MODAL"}
        assert first.processed.is_file()
        assert not second.processed.exists()
        assert runtime.active
        assert runtime.current_frame == 1
        assert runtime.completed_work == 1
        assert runtime.progress == 0.5
        assert runtime.status == ("Processing: Full Frame / Trail (processed frame 1 of 2)")
        assert settings.status == "Starting: Full Frame / Trail"
        assert not object_datamosh_ops.process_sequence.poll()

        owned_timer_event = type(
            "OwnedTimerEvent",
            (),
            {"type": "TIMER", "timer": modal_window_manager.timer},
        )()
        assert process_operator.modal(modal_context, owned_timer_event) == {"FINISHED"}
        assert second.processed.is_file()
        assert not runtime.active
        assert runtime.phase == "COMPLETED"
        assert runtime.current_frame == 2
        assert runtime.completed_work == 2
        assert runtime.progress == 1.0
        assert runtime.status == (
            f"Processed 2 frame(s) with Full Frame / Trail; report: {runtime.manifest_path}"
        )
        manifest = json.loads(Path(runtime.manifest_path).read_text(encoding="utf-8"))
        assert manifest["schema_version"] == 4
        assert manifest["history_source"] == "FULL_FRAME"
        assert manifest["effective_settings"]["history_source"] == "FULL_FRAME"
        assert manifest["effective_settings"]["mode"] == "TRAIL"
        assert manifest["effective_settings"]["extension_version"] == "0.1.0"
        assert manifest["effective_settings"]["blender_version"] == bpy.app.version_string
        assert modal_window_manager.events[-2:] == [
            ("timer_remove", modal_window_manager.timer),
            ("progress_end", None),
        ]
        assert exr_contract(second.processed) == ((2, 3), (2, 2, 2, 2))
        processed = image_io.read_rgba(second.processed)
        assert np.allclose(processed[:, 1], first_beauty[:, 1], atol=1e-6)
        assert np.allclose(processed[:, (0, 2)], second_beauty[:, (0, 2)], atol=1e-6)
        assert len(bpy.data.images) == processing_images_before
        try:
            object_datamosh_ops.process_sequence()
        except RuntimeError as error:
            assert "overwrite is disabled" in str(error)
        else:
            raise AssertionError("processing overwrote existing outputs without permission")
        assert "overwrite is disabled" in settings.status

        cancelled_processing_paths = SequencePaths(Path(temp_directory) / "cancelled")
        for frame_paths, beauty in (
            (cancelled_processing_paths.frame(1), first_beauty),
            (cancelled_processing_paths.frame(2), second_beauty),
        ):
            image_io.write_rgba(frame_paths.beauty, beauty)
            image_io.write_rgba(frame_paths.vector, zero_vector)
            image_io.write_rgba(frame_paths.matte, matte_rgba)
        settings.output_directory = str(cancelled_processing_paths.root)
        cancelled_window_manager = ModalWindowManagerRecorder()
        cancelled_context = type(
            "CancelledModalContext",
            (),
            {
                "scene": scene,
                "window_manager": cancelled_window_manager,
                "window": object(),
            },
        )()
        cancelled_operator = ProcessOperatorHarness(ODM_OT_process_sequence)
        assert cancelled_operator.execute(cancelled_context) == {"RUNNING_MODAL"}
        assert cancelled_operator.modal(cancelled_context, timer_event) == {"PASS_THROUGH"}
        cancelled_timer_event = type(
            "CancelledTimerEvent",
            (),
            {"type": "TIMER", "timer": cancelled_window_manager.timer},
        )()
        assert cancelled_operator.modal(cancelled_context, cancelled_timer_event) == {
            "RUNNING_MODAL"
        }
        assert object_datamosh_ops.cancel_operation() == {"FINISHED"}
        assert runtime.active
        assert runtime.cancel_requested
        assert runtime.phase == "CANCELLING"
        assert runtime.status == "Cancel requested; waiting for a safe boundary..."
        assert cancelled_operator.modal(cancelled_context, cancelled_timer_event) == {"CANCELLED"}
        assert not runtime.active
        assert not runtime.cancel_requested
        assert runtime.phase == "CANCELLED"
        assert runtime.status == "Cancelled after 1 frame(s)"
        assert cancelled_processing_paths.frame(1).processed.is_file()
        assert not cancelled_processing_paths.frame(2).processed.exists()
        recovery_manifest = json.loads(
            (
                cancelled_processing_paths.root / "processed" / "ODM_sequence_manifest.json"
            ).read_text(encoding="utf-8")
        )
        assert recovery_manifest["completed_frames"] == [1]
        assert cancelled_window_manager.events[-2:] == [
            ("timer_remove", cancelled_window_manager.timer),
            ("progress_end", None),
        ]
        assert object_datamosh_ops.process_sequence.poll()

        settings.sequence_run_mode = "RESUME"
        resumed_window_manager = ModalWindowManagerRecorder()
        resumed_context = type(
            "ResumedModalContext",
            (),
            {
                "scene": scene,
                "window_manager": resumed_window_manager,
                "window": object(),
            },
        )()
        resumed_operator = ProcessOperatorHarness(ODM_OT_process_sequence)
        assert resumed_operator.execute(resumed_context) == {"RUNNING_MODAL"}
        assert runtime.current_frame == 2
        assert runtime.completed_work == 1
        assert runtime.progress == 0.5
        resumed_timer_event = type(
            "ResumedTimerEvent",
            (),
            {"type": "TIMER", "timer": resumed_window_manager.timer},
        )()
        assert resumed_operator.modal(resumed_context, resumed_timer_event) == {"FINISHED"}
        assert cancelled_processing_paths.frame(2).processed.is_file()
        assert not runtime.active
        assert runtime.phase == "COMPLETED"
        assert runtime.completed_work == 2
        assert runtime.progress == 1.0
        settings.sequence_run_mode = "REPROCESS"

        escape_paths = SequencePaths(Path(temp_directory) / "escape")
        escape_frame = escape_paths.frame(1)
        image_io.write_rgba(escape_frame.beauty, first_beauty)
        image_io.write_rgba(escape_frame.vector, zero_vector)
        image_io.write_rgba(escape_frame.matte, matte_rgba)
        settings.output_directory = str(escape_paths.root)
        settings.frame_end = 1
        escape_window_manager = ModalWindowManagerRecorder()
        escape_context = type(
            "EscapeModalContext",
            (),
            {
                "scene": scene,
                "window_manager": escape_window_manager,
                "window": object(),
            },
        )()
        escape_operator = ProcessOperatorHarness(ODM_OT_process_sequence)
        escape_event = type("EscapeEvent", (), {"type": "ESC"})()
        assert escape_operator.execute(escape_context) == {"RUNNING_MODAL"}
        assert escape_operator.modal(escape_context, escape_event) == {"RUNNING_MODAL"}
        assert runtime.active
        assert runtime.cancel_requested
        assert runtime.phase == "CANCELLING"
        escape_timer_event = type(
            "EscapeTimerEvent",
            (),
            {"type": "TIMER", "timer": escape_window_manager.timer},
        )()
        assert escape_operator.modal(escape_context, escape_timer_event) == {"CANCELLED"}
        assert not runtime.active
        assert runtime.phase == "CANCELLED"
        assert not escape_frame.processed.exists()

        failed_paths = SequencePaths(Path(temp_directory) / "failed")
        failed_first = failed_paths.frame(1)
        image_io.write_rgba(failed_first.beauty, first_beauty)
        image_io.write_rgba(failed_first.vector, zero_vector)
        image_io.write_rgba(failed_first.matte, matte_rgba)
        settings.output_directory = str(failed_paths.root)
        settings.frame_end = 2
        failed_window_manager = ModalWindowManagerRecorder()
        failed_context = type(
            "FailedModalContext",
            (),
            {
                "scene": scene,
                "window_manager": failed_window_manager,
                "window": object(),
            },
        )()
        failed_operator = ProcessOperatorHarness(ODM_OT_process_sequence)
        assert failed_operator.execute(failed_context) == {"RUNNING_MODAL"}
        failed_timer_event = type(
            "FailedTimerEvent",
            (),
            {"type": "TIMER", "timer": failed_window_manager.timer},
        )()
        assert failed_operator.modal(failed_context, failed_timer_event) == {"RUNNING_MODAL"}
        assert failed_operator.modal(failed_context, failed_timer_event) == {"CANCELLED"}
        assert failed_first.processed.is_file()
        assert not runtime.active
        assert runtime.phase == "FAILED"
        assert runtime.current_frame == 2
        assert runtime.completed_work == 1
        assert "Processing failed during processing at frame 2" in runtime.status
        assert failed_window_manager.events[-2:] == [
            ("timer_remove", failed_window_manager.timer),
            ("progress_end", None),
        ]
        assert object_datamosh_ops.process_sequence.poll()

        callback_paths = SequencePaths(Path(temp_directory) / "framework-cancel")
        callback_frame = callback_paths.frame(1)
        image_io.write_rgba(callback_frame.beauty, first_beauty)
        image_io.write_rgba(callback_frame.vector, zero_vector)
        image_io.write_rgba(callback_frame.matte, matte_rgba)
        settings.output_directory = str(callback_paths.root)
        settings.frame_end = 1
        callback_window_manager = ModalWindowManagerRecorder()
        callback_context = type(
            "CallbackModalContext",
            (),
            {
                "scene": scene,
                "window_manager": callback_window_manager,
                "window": object(),
            },
        )()
        callback_operator = ProcessOperatorHarness(ODM_OT_process_sequence)
        assert callback_operator.execute(callback_context) == {"RUNNING_MODAL"}
        other_scene = bpy.data.scenes.new("ODM_Other_Scene")
        try:
            other_context = type("OtherSceneContext", (), {"scene": other_scene})()
            assert not ODM_OT_process_sequence.poll(cast(Any, other_context))
            callback_operator.cancel(callback_context)
        finally:
            bpy.data.scenes.remove(other_scene)
        assert not runtime.active
        assert runtime.phase == "CANCELLED"
        assert callback_window_manager.events[-2:] == [
            ("timer_remove", callback_window_manager.timer),
            ("progress_end", None),
        ]

        progress_failure_paths = SequencePaths(Path(temp_directory) / "progress-failure")
        progress_failure_frame = progress_failure_paths.frame(1)
        image_io.write_rgba(progress_failure_frame.beauty, first_beauty)
        image_io.write_rgba(progress_failure_frame.vector, zero_vector)
        image_io.write_rgba(progress_failure_frame.matte, matte_rgba)
        settings.output_directory = str(progress_failure_paths.root)
        progress_failure_window_manager = ModalWindowManagerRecorder(fail_progress_update_at=2)
        progress_failure_context = type(
            "ProgressFailureModalContext",
            (),
            {
                "scene": scene,
                "window_manager": progress_failure_window_manager,
                "window": object(),
            },
        )()
        progress_failure_operator = ProcessOperatorHarness(ODM_OT_process_sequence)
        assert progress_failure_operator.execute(progress_failure_context) == {"RUNNING_MODAL"}
        progress_failure_timer_event = type(
            "ProgressFailureTimerEvent",
            (),
            {"type": "TIMER", "timer": progress_failure_window_manager.timer},
        )()
        assert progress_failure_operator.modal(
            progress_failure_context, progress_failure_timer_event
        ) == {"CANCELLED"}
        assert not runtime.active
        assert runtime.phase == "FAILED"
        assert "progress publication failed" in runtime.status
        assert progress_failure_window_manager.events[-2:] == [
            ("timer_remove", progress_failure_window_manager.timer),
            ("progress_end", None),
        ]

        restart_paths = SequencePaths(Path(temp_directory) / "restart-after-failure")
        restart_frame = restart_paths.frame(1)
        image_io.write_rgba(restart_frame.beauty, first_beauty)
        image_io.write_rgba(restart_frame.vector, zero_vector)
        image_io.write_rgba(restart_frame.matte, matte_rgba)
        settings.output_directory = str(restart_paths.root)
        restart_window_manager = ModalWindowManagerRecorder()
        restart_context = type(
            "RestartModalContext",
            (),
            {
                "scene": scene,
                "window_manager": restart_window_manager,
                "window": object(),
            },
        )()
        restart_operator = ProcessOperatorHarness(ODM_OT_process_sequence)
        assert restart_operator.execute(restart_context) == {"RUNNING_MODAL"}
        restart_operator.cancel(restart_context)
        assert not runtime.active

        schema_v2_paths = SequencePaths(Path(temp_directory) / "schema-v2")
        schema_v2_manifest = schema_v2_paths.root / "processed" / "ODM_sequence_manifest.json"
        schema_v2_manifest.parent.mkdir(parents=True)
        schema_v2_manifest.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "frame_start": 1,
                    "frame_end": 1,
                    "history_source": "TARGET_ONLY",
                    "settings_fingerprint": "opaque-v2",
                    "completed_frames": [1],
                }
            ),
            encoding="utf-8",
        )
        settings.output_directory = str(schema_v2_paths.root)
        settings.frame_start = 1
        settings.frame_end = 1
        settings.sequence_run_mode = "RESUME"
        settings.history_source = "TARGET_ONLY"
        settings.feedback_mode = "HARD_LOCALIZED"
        schema_v2_operator = ProcessOperatorHarness(ODM_OT_process_sequence)
        assert schema_v2_operator.execute(restart_context) == {"CANCELLED"}
        assert schema_v2_operator.reports
        schema_v2_report = schema_v2_operator.reports[-1][1]
        assert "schema 2 cannot prove the complete effective settings" in schema_v2_report
        assert "reprocess" in settings.status
        settings.sequence_run_mode = "REPROCESS"

        print(
            "Sequence processing outputs:",
            ", ".join(path.name for path in (first.processed, second.processed)),
        )
