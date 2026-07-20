"""Blender background smoke test for the extension's public registration seam."""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

import bpy

if not hasattr(bpy, "app"):
    import pytest

    pytest.skip("requires Blender's Python runtime", allow_module_level=True)

import numpy as np

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
TEST_ROOT = REPOSITORY_ROOT / "tests"
for import_root in (SOURCE_ROOT, TEST_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from blender_combined_modal_smoke import run_combined_modal_scenario  # noqa: E402
from blender_modal_test_support import LayoutRecorder  # noqa: E402
from blender_processing_modal_smoke import run_processing_modal_scenarios  # noqa: E402
from blender_raw_render_modal_smoke import run_raw_render_modal_scenarios  # noqa: E402

import object_datamosh  # noqa: E402
from object_datamosh.blender_image_io import (  # noqa: E402
    BlenderImageIO,
    blender_pixels_to_canonical,
    canonical_to_blender_pixels,
)
from object_datamosh.compositor_setup import (  # noqa: E402
    restore_object_index_passes,
    setup_object_index_passes,
)
from object_datamosh.core.contracts import FeedbackSettings  # noqa: E402
from object_datamosh.core.exr import read_full_float_rgba  # noqa: E402
from object_datamosh.core.feedback import process_frame  # noqa: E402
from object_datamosh.core.paths import SequencePaths  # noqa: E402
from object_datamosh.raw_render import (  # noqa: E402
    RawRenderCancelled,
    render_raw_passes,
)
from object_datamosh.ui import (  # noqa: E402
    ODM_RuntimeState,
    _draw_sidebar,
    _WindowManagerProgress,
    feedback_settings_for_scene,
    runtime_for_scene,
    sequence_paths_for_scene,
    settings_for_scene,
)


def exr_contract(path: Path) -> tuple[tuple[int, int], tuple[int, ...]]:
    """Read dimensions and channel pixel types from an OpenEXR header."""
    data = path.read_bytes()
    assert data[:4] == b"v/1\x01"
    position = 8
    attributes: dict[str, bytes] = {}

    def read_c_string() -> str:
        nonlocal position
        end = data.index(0, position)
        value = data[position:end].decode("ascii")
        position = end + 1
        return value

    while name := read_c_string():
        read_c_string()  # Attribute type.
        size = struct.unpack_from("<I", data, position)[0]
        position += 4
        attributes[name] = data[position : position + size]
        position += size

    minimum_x, minimum_y, maximum_x, maximum_y = struct.unpack("<4i", attributes["dataWindow"])
    channel_list = attributes["channels"]
    channel_position = 0
    pixel_types: list[int] = []
    while channel_list[channel_position]:
        channel_position = channel_list.index(0, channel_position) + 1
        pixel_types.append(struct.unpack_from("<i", channel_list, channel_position)[0])
        channel_position += 16
    return (
        (maximum_y - minimum_y + 1, maximum_x - minimum_x + 1),
        tuple(pixel_types),
    )


def run_multilayer_orientation_smoke(expected: np.ndarray, image_io: BlenderImageIO) -> None:
    """Prove compositor multilayer scanlines and all raw passes share canonical coordinates."""
    images_before = len(bpy.data.images)
    scenes_before = len(bpy.data.scenes)
    node_groups_before = len(bpy.data.node_groups)
    cameras_before = len(bpy.data.cameras)
    objects_before = len(bpy.data.objects)
    height, width, _channels = expected.shape
    vector = np.ascontiguousarray(expected[..., [2, 0, 1, 3]], dtype=np.float32)
    matte = np.empty_like(expected)
    matte[..., 0] = expected[..., 0]
    matte[..., 1] = expected[..., 2]
    matte[..., 2] = expected[..., 1]
    matte[..., 3] = expected[..., 3]
    passes = {"beauty": expected, "vector": vector, "matte": matte}

    scene = bpy.data.scenes.new("ODM_Orientation_Smoke")
    camera_data = bpy.data.cameras.new("ODM_Orientation_Smoke_Camera")
    camera = bpy.data.objects.new("ODM_Orientation_Smoke_Camera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    tree = bpy.data.node_groups.new("ODM_Orientation_Smoke_Tree", "CompositorNodeTree")
    scene.compositing_node_group = tree
    created_images: list[Any] = []
    try:
        cast(Any, scene.render).engine = "BLENDER_WORKBENCH"
        scene.render.resolution_x = width
        scene.render.resolution_y = height
        scene.render.resolution_percentage = 100
        with tempfile.TemporaryDirectory(prefix="ODM_orientation_smoke_") as temporary:
            root = Path(temporary)
            for pass_name, pixels in passes.items():
                image = bpy.data.images.new(
                    f"ODM_Orientation_{pass_name}",
                    width=width,
                    height=height,
                    alpha=True,
                    float_buffer=True,
                )
                created_images.append(image)
                cast(Any, image.colorspace_settings).name = "Linear Rec.709"
                cast(Any, image.pixels).foreach_set(canonical_to_blender_pixels(pixels))
                raw_pixels = np.empty(pixels.size, dtype=np.float32)
                cast(Any, image.pixels).foreach_get(raw_pixels)
                np.testing.assert_array_equal(
                    blender_pixels_to_canonical(raw_pixels, width=width, height=height), pixels
                )

                image_node = cast(Any, tree.nodes.new("CompositorNodeImage"))
                image_node.image = image
                output = cast(Any, tree.nodes.new("CompositorNodeOutputFile"))
                output.directory = str(root)
                output.file_name = f"ODM_{pass_name}_####"
                output.format.file_format = "OPEN_EXR_MULTILAYER"
                output.format.color_mode = "RGBA"
                output.format.color_depth = "32"
                output.format.exr_codec = "ZIP"
                output.save_as_render = False
                output.file_output_items.clear()
                item = output.file_output_items.new("RGBA", "Image")
                item.override_node_format = False
                item.save_as_render = False
                tree.links.new(image_node.outputs["Image"], output.inputs["Image"])

            scene.frame_set(1)
            bpy.ops.render.render(scene=scene.name)
            decoded: dict[str, np.ndarray] = {}
            for pass_name, pixels in passes.items():
                path = root / f"ODM_{pass_name}_0001.exr"
                assert path.is_file()
                decoded[pass_name] = read_full_float_rgba(path)
                np.testing.assert_allclose(decoded[pass_name], pixels, atol=1e-6)
            np.testing.assert_allclose(
                image_io.read_mask(root / "ODM_matte_0001.exr"), expected[..., 0], atol=1e-6
            )
            # Every asymmetric marker occupies the same X/Y in beauty, Vector, and matte.
            np.testing.assert_array_equal(decoded["beauty"][..., 0], decoded["matte"][..., 0])
            np.testing.assert_array_equal(decoded["beauty"][..., 2], decoded["vector"][..., 0])
    finally:
        scene.compositing_node_group = None
        bpy.data.scenes.remove(scene)
        bpy.data.node_groups.remove(tree)
        for image in created_images:
            bpy.data.images.remove(image)
        bpy.data.objects.remove(camera)
        bpy.data.cameras.remove(camera_data)

    assert len(bpy.data.images) == images_before
    assert len(bpy.data.scenes) == scenes_before
    assert len(bpy.data.node_groups) == node_groups_before
    assert len(bpy.data.cameras) == cameras_before
    assert len(bpy.data.objects) == objects_before


class ProgressRecorder:
    """Render-progress boundary recorder used by the raw-render smoke checks."""

    def __init__(self) -> None:
        self.events: list[tuple[str, int]] = []

    def begin(self, total: int) -> None:
        self.events.append(("begin", total))

    def update(self, completed: int) -> None:
        self.events.append(("update", completed))

    def end(self) -> None:
        self.events.append(("end", 0))


def main() -> None:
    class FailingProgressUpdate:
        def progress_update(self, completed: int) -> None:
            raise RuntimeError("progress update failed")

    failing_progress = _WindowManagerProgress(FailingProgressUpdate())
    try:
        failing_progress.update(1)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Window-manager progress failure did not propagate")
    assert failing_progress.completed == 0

    object_datamosh.register()
    object_datamosh.register()
    assert hasattr(bpy.types.Scene, "ODM_settings")
    assert hasattr(bpy.types.Scene, "ODM_runtime")
    registered_types = cast(Any, bpy.types)
    panel_type = registered_types.ODM_PT_sidebar
    assert panel_type.bl_category == "Object Datamosh"
    runtime_type = ODM_RuntimeState
    for property_name in (
        "active",
        "cancel_requested",
        "phase",
        "run_identity",
        "current_frame",
        "frame_start",
        "frame_end",
        "completed_work",
        "total_work",
        "phase_completed_work",
        "phase_total_work",
        "progress",
        "status",
        "configuration_summary",
        "manifest_path",
    ):
        assert runtime_type.bl_rna.properties[property_name].is_skip_save

    scene = bpy.context.scene
    assert scene is not None
    cast(Any, scene.render).engine = "CYCLES"
    settings = settings_for_scene(scene)
    runtime = runtime_for_scene(scene)
    assert settings.status == "Ready"
    assert not runtime.active
    assert not runtime.cancel_requested
    assert runtime.phase == "IDLE"
    assert runtime.run_identity == ""
    assert runtime.current_frame == 0
    assert runtime.frame_start == 0
    assert runtime.frame_end == 0
    assert runtime.completed_work == 0
    assert runtime.total_work == 0
    assert runtime.phase_completed_work == 0
    assert runtime.phase_total_work == 0
    assert runtime.progress == 0.0
    assert runtime.status == "Ready"
    assert settings.matte_source == "OBJECT_INDEX"
    assert settings.target_object is None
    feedback_settings = feedback_settings_for_scene(scene)
    assert feedback_settings.mode.value == "HARD_LOCALIZED"
    assert feedback_settings.history_source.value == "TARGET_ONLY"
    assert settings.history_source == "TARGET_ONLY"
    history_property = cast(Any, type(settings).bl_rna.properties["history_source"])
    history_items = {item.identifier: item for item in history_property.enum_items}
    assert history_items["TARGET_ONLY"].name == "Target Only (Legacy / Stable)"
    assert "prior target/effect coverage" in history_items["TARGET_ONLY"].description
    assert "preserves more object identity" in history_items["TARGET_ONLY"].description
    assert history_items["FULL_FRAME"].name == "Full Frame (Extreme)"
    assert "entire previous processed frame" in history_items["FULL_FRAME"].description
    assert "effect mask controls only where it appears" in history_items["FULL_FRAME"].description
    settings.history_source = "FULL_FRAME"
    assert feedback_settings_for_scene(scene).history_source.value == "FULL_FRAME"
    settings.history_source = "TARGET_ONLY"
    assert abs(feedback_settings.trail_decay - FeedbackSettings().trail_decay) < 1e-6
    assert abs(feedback_settings.persistence - FeedbackSettings().persistence) < 1e-6
    assert feedback_settings.block_size == 16
    assert feedback_settings.motion_channels.value == "RG"
    assert feedback_settings.matte_source.value == "OBJECT_INDEX"
    active_object = bpy.context.active_object
    assert active_object is not None
    object_datamosh_ops = cast(Any, bpy.ops).object_datamosh
    assert object_datamosh_ops.use_active_object() == {"FINISHED"}
    assert settings.target_object == active_object
    assert settings.status == f"Target set to {active_object.name}"

    settings.matte_source = "EXTERNAL"
    layout = LayoutRecorder()
    _draw_sidebar(layout, bpy.context, scene)
    assert layout.properties == {
        "target_object",
        "frame_start",
        "frame_end",
        "output_directory",
        "overwrite_raw",
        "overwrite_processed",
        "sequence_run_mode",
        "reset_frames",
        "resolution_change",
        "matte_source",
        "external_matte_directory",
        "feedback_mode",
        "history_source",
        "trail_decay",
        "persistence",
        "block_size",
        "motion_channels",
        "reverse_motion",
        "flip_x",
        "flip_y",
        "motion_gain",
        "motion_clamp",
        "motion_quantization",
        "diffusion",
        "refresh_probability",
        "seed",
    }
    settings.matte_source = "OBJECT_INDEX"
    _draw_sidebar(layout, bpy.context, scene)
    assert layout.operators == {
        "object_datamosh.use_active_object",
        "object_datamosh.setup_object_index",
        "object_datamosh.restore_object_index",
        "object_datamosh.render_raw_passes",
        "object_datamosh.render_and_process",
        "object_datamosh.process_sequence",
        "object_datamosh.create_vector_calibration",
        "object_datamosh.extreme_full_frame_feedback",
    }
    for guidance in (
        "Full-frame history is OFF.",
        "Background and unrelated screen content cannot become history color",
        "inside the target.",
        "First/reset frame:",
        "Visible object seeds its clean image.",
        "Background-only pre-roll:",
        "Enables a more corrupted entrance.",
    ):
        assert guidance in layout.labels
    assert any("starting point" in label and "vary by scene" in label for label in layout.labels)
    assert any(label.startswith("Active: Target Only / Hard Localized") for label in layout.labels)
    assert any(label.startswith("View Layer: ") for label in layout.labels)
    assert any(label.startswith("Output: ") for label in layout.labels)
    assert any(label.startswith("Status: ") for label in layout.labels)
    assert "Operation: Idle" in layout.labels
    assert "Phase: Idle" in layout.labels
    assert "Frame Range: 0-0" in layout.labels
    assert "Current Frame: 0" in layout.labels
    assert "Phase Work: 0/0" in layout.labels
    assert "Overall Work: 0/0" in layout.labels
    assert "Progress: 0%" in layout.labels
    assert "Save the blend file to use a project-relative output directory." in layout.labels

    runtime.active = True
    runtime.phase = "PROCESSING"
    runtime.frame_start = 1
    runtime.frame_end = 4
    runtime.current_frame = 2
    runtime.completed_work = 1
    runtime.total_work = 4
    runtime.phase_completed_work = 1
    runtime.phase_total_work = 4
    runtime.progress = 0.25
    runtime.status = "Processing frame 2 of 4"
    active_layout = LayoutRecorder()
    _draw_sidebar(active_layout, bpy.context, scene)
    assert "Operation: Active" in active_layout.labels
    assert "object_datamosh.cancel_operation" in active_layout.operators
    assert active_layout.boxes[0].enabled
    assert all(not box.enabled for box in active_layout.boxes[1:])
    assert not object_datamosh_ops.use_active_object.poll()
    assert not object_datamosh_ops.setup_object_index.poll()
    assert not object_datamosh_ops.create_vector_calibration.poll()
    assert not object_datamosh_ops.render_raw_passes.poll()
    assert not object_datamosh_ops.render_and_process.poll()
    assert not object_datamosh_ops.process_sequence.poll()
    assert not object_datamosh_ops.restore_object_index.poll()
    assert not object_datamosh_ops.extreme_full_frame_feedback.poll()
    assert object_datamosh_ops.cancel_operation() == {"FINISHED"}
    assert runtime.active
    assert runtime.cancel_requested
    assert runtime.phase == "CANCELLING"
    assert runtime.status == "Cancel requested; waiting for a safe boundary..."
    runtime.active = False
    runtime.cancel_requested = False
    runtime.phase = "IDLE"
    runtime.status = "Ready"

    effect_before_extreme_setup = (
        settings.history_source,
        settings.feedback_mode,
        settings.persistence,
        settings.trail_decay,
        settings.refresh_probability,
        settings.block_size,
        settings.motion_quantization,
        settings.diffusion,
    )
    unrelated_before_extreme_setup = (
        settings.target_object,
        settings.frame_start,
        settings.frame_end,
        settings.output_directory,
        settings.matte_source,
        settings.motion_channels,
        settings.reverse_motion,
        settings.flip_x,
        settings.flip_y,
        settings.motion_gain,
        settings.motion_clamp,
        settings.seed,
    )
    assert object_datamosh_ops.extreme_full_frame_feedback() == {"FINISHED"}
    assert settings.history_source == "FULL_FRAME"
    assert settings.feedback_mode == "TRAIL"
    assert abs(settings.persistence - 1.0) < 1e-6
    assert abs(settings.trail_decay - 0.98) < 1e-6
    assert abs(settings.refresh_probability - 0.01) < 1e-6
    assert settings.block_size == 32
    assert abs(settings.motion_quantization - 8.0) < 1e-6
    assert abs(settings.diffusion - 2.0) < 1e-6
    assert settings.status == "Applied Extreme Full-Frame Feedback starting configuration"
    full_frame_layout = LayoutRecorder()
    _draw_sidebar(full_frame_layout, bpy.context, scene)
    assert (
        "The complete previous processed frame is available as history color."
        in full_frame_layout.labels
    )
    assert any(label.startswith("Active: Full Frame / Trail") for label in full_frame_layout.labels)
    assert unrelated_before_extreme_setup == (
        settings.target_object,
        settings.frame_start,
        settings.frame_end,
        settings.output_directory,
        settings.matte_source,
        settings.motion_channels,
        settings.reverse_motion,
        settings.flip_x,
        settings.flip_y,
        settings.motion_gain,
        settings.motion_clamp,
        settings.seed,
    )
    assert object_datamosh_ops.extreme_full_frame_feedback() == {"FINISHED"}
    (
        settings.history_source,
        settings.feedback_mode,
        settings.persistence,
        settings.trail_decay,
        settings.refresh_probability,
        settings.block_size,
        settings.motion_quantization,
        settings.diffusion,
    ) = effect_before_extreme_setup

    scenes_before_calibration = set(bpy.data.scenes)
    active_scene_before_calibration = bpy.context.scene
    active_objects_before_calibration = tuple(scene.objects)
    assert object_datamosh_ops.create_vector_calibration() == {"FINISHED"}
    calibration_scenes = set(bpy.data.scenes) - scenes_before_calibration
    assert len(calibration_scenes) == 1
    calibration_scene = calibration_scenes.pop()
    assert calibration_scene.name.startswith("ODM_Vector_Calibration")
    calibration_settings = settings_for_scene(calibration_scene)
    assert (
        calibration_settings.target_object == calibration_scene.objects["ODM_Calibration_Rectangle"]
    )
    assert calibration_settings.frame_start == 1
    assert calibration_settings.frame_end == 8
    assert bpy.context.scene == active_scene_before_calibration
    assert tuple(scene.objects) == active_objects_before_calibration
    assert settings.status.startswith("Created ODM_Vector_Calibration")

    unsaved_paths = sequence_paths_for_scene(scene)
    assert unsaved_paths.root == Path(bpy.app.tempdir) / "ODM_object_datamosh_unsaved"
    assert unsaved_paths.warning is not None
    settings.output_directory = "//ODM_relative_output"
    relative_unsaved_paths = sequence_paths_for_scene(scene)
    assert relative_unsaved_paths.root == unsaved_paths.root
    assert relative_unsaved_paths.warning is not None
    settings.output_directory = str(Path(bpy.app.tempdir) / "ODM_custom_output")
    custom_paths = sequence_paths_for_scene(scene)
    assert custom_paths.root == Path(bpy.app.tempdir) / "ODM_custom_output"
    assert custom_paths.warning == (
        "Blend file is unsaved; using the explicit absolute output directory."
    )

    saved_blend = Path(bpy.app.tempdir) / "ODM_smoke.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(saved_blend))
    settings.output_directory = "//ODM_relative_output"
    saved_relative_paths = sequence_paths_for_scene(scene)
    assert saved_relative_paths.root == saved_blend.parent / "ODM_relative_output"
    assert saved_relative_paths.warning is None

    view_layer = bpy.context.view_layer
    assert view_layer is not None
    target_object = settings.target_object
    assert target_object is not None
    original_pass_index = target_object.pass_index
    original_vector_state = view_layer.use_pass_vector
    original_object_index_state = view_layer.use_pass_object_index
    user_tree = bpy.data.node_groups.new("User Compositor", "CompositorNodeTree")
    scene.compositing_node_group = user_tree
    user_node = user_tree.nodes.new("CompositorNodeBlur")
    user_node.name = "User Node"

    other_scene = bpy.data.scenes.new("ODM_Other_Scene")
    try:
        other_view_layer = other_scene.view_layers[0]
        try:
            setup_object_index_passes(scene, other_view_layer, target_object, saved_relative_paths)
        except ValueError as error:
            assert "View layer must belong" in str(error)
        else:
            raise AssertionError("Object Index setup accepted another scene's view layer")
        assert len(user_tree.nodes) == 1
    finally:
        bpy.data.scenes.remove(other_scene)

    conflicting_node = user_tree.nodes.new("CompositorNodeRLayers")
    conflicting_node.name = "ODM_Render_Layers"
    try:
        setup_object_index_passes(scene, view_layer, target_object, saved_relative_paths)
    except RuntimeError as error:
        assert "node name is already in use" in str(error)
    else:
        raise AssertionError("Object Index setup accepted a conflicting user node name")
    assert target_object.pass_index == original_pass_index
    assert view_layer.use_pass_vector == original_vector_state
    assert view_layer.use_pass_object_index == original_object_index_state
    assert user_tree.nodes.get("ODM_Object_Index_Setup") is None
    assert user_tree.nodes.get("ODM_Render_Layers") == conflicting_node
    user_tree.nodes.remove(conflicting_node)

    setup = setup_object_index_passes(scene, view_layer, target_object, saved_relative_paths)
    assert setup.pass_index > 0
    assert target_object.pass_index == setup.pass_index
    assert view_layer.use_pass_vector
    assert view_layer.use_pass_object_index
    assert setup.node_names == (
        "ODM_Object_Index_Setup",
        "ODM_Render_Layers",
        "ODM_ID_Mask",
        "ODM_Beauty_Output",
        "ODM_Vector_Output",
        "ODM_Matte_Output",
    )
    assert len(user_tree.nodes) == 7
    assert len(user_tree.links) == 4
    alternate_view_layer = scene.view_layers.new("ODM_Alternate_View_Layer")
    try:
        assert not alternate_view_layer.use_pass_vector
        assert not alternate_view_layer.use_pass_object_index
        try:
            setup_object_index_passes(
                scene, alternate_view_layer, target_object, saved_relative_paths
            )
        except RuntimeError as error:
            assert "restore it before changing view layer" in str(error)
        else:
            raise AssertionError("Object Index setup accepted a different view layer")
        assert not alternate_view_layer.use_pass_vector
        assert not alternate_view_layer.use_pass_object_index
    finally:
        scene.view_layers.remove(alternate_view_layer)
    alternate_target = scene.objects.get("Camera")
    assert alternate_target is not None
    alternate_pass_index = alternate_target.pass_index
    try:
        setup_object_index_passes(scene, view_layer, alternate_target, saved_relative_paths)
    except RuntimeError as error:
        assert "restore it before changing target" in str(error)
    else:
        raise AssertionError("Object Index setup accepted a different target without restoration")
    assert alternate_target.pass_index == alternate_pass_index
    repeated_setup = setup_object_index_passes(
        scene, view_layer, target_object, saved_relative_paths
    )
    assert repeated_setup == setup
    assert len(user_tree.nodes) == 7
    assert len(user_tree.links) == 4

    renamed_frame = user_tree.nodes.get("ODM_Object_Index_Setup")
    renamed_render_layers = user_tree.nodes.get("ODM_Render_Layers")
    assert renamed_frame is not None
    assert renamed_render_layers is not None
    renamed_frame.name = "User Renamed ODM Frame"
    renamed_render_layers.name = "User Renamed ODM Render Layers"
    assert (
        setup_object_index_passes(scene, view_layer, target_object, saved_relative_paths) == setup
    )
    assert len(user_tree.nodes) == 7
    assert user_tree.nodes.get("ODM_Object_Index_Setup") == renamed_frame
    assert user_tree.nodes.get("ODM_Render_Layers") == renamed_render_layers

    assert restore_object_index_passes(scene)
    assert target_object.pass_index == original_pass_index
    assert view_layer.use_pass_vector == original_vector_state
    assert view_layer.use_pass_object_index == original_object_index_state
    assert scene.compositing_node_group == user_tree
    assert user_tree.nodes.get("User Node") == user_node
    assert len(user_tree.nodes) == 1
    assert all(user_tree.nodes.get(name) is None for name in setup.node_names)
    assert not restore_object_index_passes(scene)

    assert object_datamosh_ops.setup_object_index() == {"FINISHED"}
    assert settings.status.startswith("Object Index setup ready")
    assert user_tree.nodes.get("ODM_Object_Index_Setup") is not None
    assert object_datamosh_ops.restore_object_index() == {"FINISHED"}
    assert settings.status == "Object Index setup restored"
    assert user_tree.nodes.get("ODM_Object_Index_Setup") is None

    with tempfile.TemporaryDirectory(prefix="ODM_compositor_smoke_") as temp_directory:
        temp_root = Path(temp_directory)
        operator_root = temp_root / "operator"
        settings.output_directory = str(operator_root)
        settings.frame_start = 1
        settings.frame_end = 1
        settings.overwrite_raw = False
        render = scene.render
        render.resolution_x = 16
        render.resolution_y = 12
        render.resolution_percentage = 100
        assert object_datamosh_ops.setup_object_index() == {"FINISHED"}
        run_raw_render_modal_scenarios(
            scene,
            settings,
            runtime,
            object_datamosh_ops,
            operator_root,
        )
        assert settings.status == "Rendered 1 raw frame(s)"
        assert object_datamosh_ops.restore_object_index() == {"FINISHED"}

        combined_root = temp_root / "combined-modal"
        combined_images_before = len(bpy.data.images)
        combined_frame_before = scene.frame_current
        assert object_datamosh_ops.setup_object_index() == {"FINISHED"}
        run_combined_modal_scenario(
            scene,
            settings,
            runtime,
            object_datamosh_ops,
            combined_root,
        )
        assert len(bpy.data.images) == combined_images_before
        assert scene.frame_current == combined_frame_before

        background_combined_root = temp_root / "combined-background"
        settings.output_directory = str(background_combined_root)
        settings.overwrite_raw = False
        settings.overwrite_processed = False
        assert object_datamosh_ops.render_and_process() == {"FINISHED"}
        background_paths = SequencePaths(background_combined_root)
        background_inventory = tuple(
            path
            for frame in (background_paths.frame(1), background_paths.frame(2))
            for path in (frame.beauty, frame.vector, frame.matte, frame.processed)
        )
        assert all(path.is_file() for path in background_inventory), background_inventory

        background_failure_root = temp_root / "combined-background-failure"
        settings.output_directory = str(background_failure_root)
        settings.frame_end = 1
        settings.matte_source = "CRYPTOMATTE"
        try:
            object_datamosh_ops.render_and_process()
        except RuntimeError as error:
            assert "failed during processing at frame 1" in str(error)
        else:
            raise AssertionError("Background processing failure did not reach Blender")
        assert "failed during processing at frame 1" in settings.status
        settings.frame_end = 2
        settings.matte_source = "OBJECT_INDEX"
        assert object_datamosh_ops.restore_object_index() == {"FINISHED"}
        print(
            "Render and Process outputs:",
            ", ".join(path.name for path in background_inventory),
        )

        configured_paths = SequencePaths(temp_root / "configured")
        render_paths = SequencePaths(temp_root / "rendered")
        setup_object_index_passes(scene, view_layer, target_object, configured_paths)
        original_frame = scene.frame_current
        beauty_node = scene.compositing_node_group.nodes.get("ODM_Beauty_Output")
        assert beauty_node is not None
        wrong_layer = scene.view_layers.new("ODM_Wrong_Raw_View_Layer")
        wrong_layer_progress = ProgressRecorder()
        try:
            try:
                render_raw_passes(
                    scene,
                    wrong_layer,
                    SequencePaths(temp_root / "wrong_layer"),
                    frame_start=1,
                    frame_end=1,
                    progress=wrong_layer_progress,
                )
            except RuntimeError as error:
                assert "set up for another view layer" in str(error)
            else:
                raise AssertionError("Raw rendering accepted a view layer other than its setup")
        finally:
            scene.view_layers.remove(wrong_layer)
        assert wrong_layer_progress.events == [("begin", 1), ("end", 0)]
        assert Path(beauty_node.directory) == configured_paths.root / "raw" / "beauty"

        camera = scene.camera
        failure_progress = ProgressRecorder()
        scene.camera = None
        try:
            try:
                render_raw_passes(
                    scene,
                    view_layer,
                    SequencePaths(temp_root / "failed"),
                    frame_start=1,
                    frame_end=1,
                    progress=failure_progress,
                )
            except RuntimeError as error:
                assert "Cannot render, no camera" in str(error)
            else:
                raise AssertionError("Raw rendering succeeded without a scene camera")
        finally:
            scene.camera = camera
        assert failure_progress.events == [("begin", 1), ("end", 0)]
        assert scene.frame_current == original_frame
        assert Path(beauty_node.directory) == configured_paths.root / "raw" / "beauty"

        progress = ProgressRecorder()
        result = render_raw_passes(
            scene,
            view_layer,
            render_paths,
            frame_start=1,
            frame_end=2,
            progress=progress,
        )
        assert tuple(frame.frame for frame in result.frames) == (1, 2)
        assert all(
            path.is_file()
            for frame_number in (1, 2)
            for path in (
                render_paths.frame(frame_number).beauty,
                render_paths.frame(frame_number).vector,
                render_paths.frame(frame_number).matte,
            )
        )
        assert scene.frame_current == original_frame
        assert progress.events == [
            ("begin", 2),
            ("update", 1),
            ("update", 2),
            ("end", 0),
        ]
        assert Path(beauty_node.directory) == configured_paths.root / "raw" / "beauty"
        emitted_paths = result.frames[0]
        actual_outputs = (
            emitted_paths.beauty,
            emitted_paths.vector,
            emitted_paths.matte,
        )
        assert all(path.is_file() for path in actual_outputs), actual_outputs
        output_contracts = tuple(exr_contract(path) for path in actual_outputs)
        assert output_contracts == (
            ((12, 16), (2, 2, 2, 2)),
            ((12, 16), (2, 2, 2, 2)),
            ((12, 16), (2, 2, 2, 2)),
        )
        try:
            render_raw_passes(
                scene,
                view_layer,
                render_paths,
                frame_start=1,
                frame_end=2,
            )
        except FileExistsError as error:
            assert "overwrite is disabled" in str(error)
        else:
            raise AssertionError("Raw rendering overwrote existing outputs without permission")

        negative_paths = SequencePaths(temp_root / "negative")
        negative_result = render_raw_passes(
            scene,
            view_layer,
            negative_paths,
            frame_start=-1,
            frame_end=-1,
        )
        assert tuple(frame.frame for frame in negative_result.frames) == (-1,)
        assert negative_result.frames[0].beauty.name == "ODM_beauty_-0001.exr"
        try:
            render_raw_passes(
                scene,
                view_layer,
                negative_paths,
                frame_start=-1,
                frame_end=-1,
            )
        except FileExistsError:
            pass
        else:
            raise AssertionError("Negative-frame output bypassed overwrite protection")

        cancelled_paths = SequencePaths(temp_root / "cancelled")
        cancel_progress = ProgressRecorder()
        try:
            render_raw_passes(
                scene,
                view_layer,
                cancelled_paths,
                frame_start=1,
                frame_end=2,
                progress=cancel_progress,
                should_cancel=lambda: ("update", 1) in cancel_progress.events,
            )
        except RawRenderCancelled as error:
            assert tuple(frame.frame for frame in error.completed_frames) == (1,)
            assert all(
                path.is_file()
                for path in (
                    error.completed_frames[0].beauty,
                    error.completed_frames[0].vector,
                    error.completed_frames[0].matte,
                )
            )
        else:
            raise AssertionError("Raw rendering ignored cancellation between frames")
        assert cancel_progress.events == [("begin", 2), ("update", 1), ("end", 0)]
        assert scene.frame_current == original_frame
        assert cancelled_paths.frame(1).beauty.is_file()
        assert not cancelled_paths.frame(2).beauty.exists()
        assert Path(beauty_node.directory) == configured_paths.root / "raw" / "beauty"
        print(
            "Object Index smoke outputs:",
            ", ".join(
                path.name
                for frame in result.frames
                for path in (frame.beauty, frame.vector, frame.matte)
            ),
        )
        restore_object_index_passes(scene)

    scene.compositing_node_group = None
    setup_object_index_passes(scene, view_layer, target_object, saved_relative_paths)
    owned_tree = scene.compositing_node_group
    assert owned_tree is not None
    owned_tree_name = owned_tree.name
    assert restore_object_index_passes(scene)
    assert scene.compositing_node_group is None
    assert bpy.data.node_groups.get(owned_tree_name) is None

    image_io = BlenderImageIO()
    run_processing_modal_scenarios(
        scene, settings, runtime, image_io, object_datamosh_ops, exr_contract
    )

    image_path = Path(bpy.app.tempdir) / "ODM_image_io_smoke.exr"
    expected = np.array(
        [
            [
                [0.05, 0.06, 0.07, 0.08],
                [0.09, 0.10, 0.11, 0.12],
                [0.13, 0.14, 0.15, 0.16],
                [0.17, 0.18, 0.19, 0.20],
                [0.21, 0.22, 0.23, 0.24],
            ],
            [
                [0.25, 0.26, 0.27, 0.28],
                [0.29, 0.30, 0.31, 0.32],
                [0.77, 0.67, 0.57, 0.47],
                [0.33, 0.34, 0.35, 0.36],
                [0.37, 0.38, 0.39, 0.40],
            ],
            [
                [0.41, 0.42, 0.43, 0.44],
                [0.45, 0.46, 0.47, 0.48],
                [0.49, 0.50, 0.51, 0.52],
                [0.53, 0.54, 0.55, 0.56],
                [0.91, 0.81, 0.71, 0.61],
            ],
        ],
        dtype=np.float32,
    )
    images_before = len(bpy.data.images)
    blender_buffer = canonical_to_blender_pixels(expected)
    assert blender_buffer.dtype == np.float32
    assert blender_buffer.shape == (expected.size,)
    np.testing.assert_array_equal(
        blender_pixels_to_canonical(blender_buffer, width=5, height=3), expected
    )
    image_settings = scene.render.image_settings
    render_settings_before = (
        image_settings.file_format,
        image_settings.color_mode,
        image_settings.color_depth,
        image_settings.exr_codec,
    )
    zero_motion = np.zeros_like(expected)
    full_matte = np.ones(expected.shape[:2], dtype=np.float32)
    identity, _state = process_frame(
        expected,
        zero_motion,
        full_matte,
        None,
        1,
        FeedbackSettings(),
        force_reset=True,
    )
    np.testing.assert_array_equal(identity, expected)
    image_io.write_rgba(image_path, identity)
    # OpenEXR scanline row zero is independently decoded as canonical displayed top.
    np.testing.assert_allclose(read_full_float_rgba(image_path), expected, atol=1e-6)
    try:
        image_io.write_rgba(image_path, cast(Any, []))
    except TypeError as error:
        assert str(error) == "pixels must be a NumPy array"
    else:
        raise AssertionError("BlenderImageIO accepted a non-array pixel value")
    try:
        image_io.write_rgba(image_path.with_suffix(".png"), expected)
    except ValueError as error:
        assert "requires an .exr path" in str(error)
    else:
        raise AssertionError("BlenderImageIO accepted a non-EXR output path")
    invalid_input_path = Path(bpy.app.tempdir) / "ODM_invalid_input.png"
    invalid_input_path.write_bytes(b"not an image")
    try:
        image_io.read_rgba(invalid_input_path)
    except ValueError as error:
        assert "requires an .exr path" in str(error)
    else:
        raise AssertionError("BlenderImageIO accepted a non-EXR input path")
    actual = image_io.read_rgba(image_path)
    external_image_path = Path(bpy.app.tempdir) / "external_matte.exr"
    shutil.copyfile(image_path, external_image_path)
    assert np.allclose(image_io.read_rgba(external_image_path), expected, atol=1e-6)
    actual_mask = image_io.read_mask(external_image_path)
    assert actual_mask.dtype == np.float32
    assert actual_mask.shape == expected.shape[:2]
    assert np.allclose(actual_mask, expected[..., 0], atol=1e-6)
    assert image_path.is_file()
    assert np.allclose(actual, expected, atol=1e-6), (actual, expected)
    run_multilayer_orientation_smoke(expected, image_io)
    assert len(bpy.data.images) == images_before
    assert (
        image_settings.file_format,
        image_settings.color_mode,
        image_settings.color_depth,
        image_settings.exr_codec,
    ) == render_settings_before

    object_datamosh.unregister()
    object_datamosh.unregister()
    assert not hasattr(bpy.types.Scene, "ODM_settings")
    assert not hasattr(bpy.types.Scene, "ODM_runtime")

    object_datamosh.register()
    object_datamosh.unregister()
    assert not hasattr(bpy.types.Scene, "ODM_settings")
    assert not hasattr(bpy.types.Scene, "ODM_runtime")

    scene_type = cast(Any, bpy.types.Scene)
    scene_type.ODM_settings = bpy.props.StringProperty(name="Foreign property")
    try:
        try:
            object_datamosh.register()
        except RuntimeError as error:
            assert "is not owned by Object Datamosh" in str(error)
        else:
            raise AssertionError("Registration accepted a foreign Scene.ODM_settings property")
        assert hasattr(scene_type, "ODM_settings")
        object_datamosh.unregister()
        assert hasattr(scene_type, "ODM_settings")
    finally:
        del scene_type.ODM_settings

    # Real bpy dispatch runs in an isolated Blender process because background mode cannot pump
    # foreground modal events while this parent script owns its main thread.
    registered_smoke = subprocess.run(
        [
            bpy.app.binary_path,
            "--background",
            "--factory-startup",
            "--python",
            str(TEST_ROOT / "blender_registered_modal_smoke.py"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert registered_smoke.returncode == 0, (
        registered_smoke.stdout,
        registered_smoke.stderr,
    )
    assert "Registered modal dispatch smoke passed" in registered_smoke.stdout

    print("Object Datamosh Blender smoke test passed")


if __name__ == "__main__":
    main()
