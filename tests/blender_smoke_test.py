"""Blender background smoke test for the extension's public registration seam."""

from __future__ import annotations

import shutil
import struct
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
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import object_datamosh  # noqa: E402
import object_datamosh.ui as ui_module  # noqa: E402
from object_datamosh.blender_image_io import BlenderImageIO  # noqa: E402
from object_datamosh.compositor_setup import (  # noqa: E402
    restore_object_index_passes,
    setup_object_index_passes,
)
from object_datamosh.core.contracts import FeedbackSettings  # noqa: E402
from object_datamosh.core.paths import SequencePaths  # noqa: E402
from object_datamosh.raw_render import (  # noqa: E402
    RawRenderCancelled,
    render_raw_passes,
)
from object_datamosh.ui import (  # noqa: E402
    _draw_sidebar,
    feedback_settings_for_scene,
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


class LayoutRecorder:
    """Minimal Blender layout double for verifying the emitted sidebar controls."""

    def __init__(self) -> None:
        self.properties: set[str] = set()
        self.operators: set[str] = set()
        self.labels: list[str] = []
        self.alert = False

    def box(self) -> LayoutRecorder:
        return self

    def row(self, *, align: bool = False) -> LayoutRecorder:
        del align
        return self

    def prop(self, data: object, property_name: str) -> None:
        del data
        self.properties.add(property_name)

    def operator(self, operator_name: str) -> None:
        self.operators.add(operator_name)

    def label(self, *, text: str, icon: str | None = None) -> None:
        del icon
        self.labels.append(text)


def main() -> None:
    object_datamosh.register()
    object_datamosh.register()
    assert hasattr(bpy.types.Scene, "ODM_settings")
    panel_type = cast(Any, bpy.types).ODM_PT_sidebar
    assert panel_type.bl_category == "Object Datamosh"

    scene = bpy.context.scene
    assert scene is not None
    settings = settings_for_scene(scene)
    assert settings.status == "Ready"
    assert settings.matte_source == "OBJECT_INDEX"
    assert settings.target_object is None
    feedback_settings = feedback_settings_for_scene(scene)
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
        "object_datamosh.process_sequence",
        "object_datamosh.create_vector_calibration",
    }
    assert any(label.startswith("View Layer: ") for label in layout.labels)
    assert any(label.startswith("Output: ") for label in layout.labels)
    assert any(label.startswith("Status: ") for label in layout.labels)
    assert "Save the blend file to use a project-relative output directory." in layout.labels

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
        render.engine = "CYCLES"
        render.resolution_x = 16
        render.resolution_y = 12
        render.resolution_percentage = 100
        assert object_datamosh_ops.setup_object_index() == {"FINISHED"}
        assert object_datamosh_ops.render_raw_passes() == {"FINISHED"}
        assert settings.status == "Rendered 1 raw frame(s)"
        assert SequencePaths(operator_root).frame(1).beauty.is_file()
        assert object_datamosh_ops.restore_object_index() == {"FINISHED"}

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
        assert wrong_layer_progress.events == []
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
        assert result.frames == (render_paths.frame(1), render_paths.frame(2))
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
            assert error.completed_frames == (cancelled_paths.frame(1),)
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
        assert object_datamosh_ops.process_sequence() == {"FINISHED"}
        assert settings.status == "Processed 2 frame(s)"
        assert first.processed.is_file()
        assert second.processed.is_file()
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

        original_process_sequence = ui_module.process_sequence

        def permission_denied(*args: Any, **kwargs: Any) -> None:
            del args, kwargs
            raise PermissionError("Processed output directory is not writable")

        try:
            ui_module.process_sequence = permission_denied
            try:
                object_datamosh_ops.process_sequence()
            except RuntimeError as error:
                assert "not writable" in str(error)
            else:
                raise AssertionError("processing did not report an output permission failure")
            assert settings.status == "Processed output directory is not writable"
        finally:
            ui_module.process_sequence = original_process_sequence

        print(
            "Sequence processing outputs:",
            ", ".join(path.name for path in (first.processed, second.processed)),
        )

    image_path = Path(bpy.app.tempdir) / "ODM_image_io_smoke.exr"
    expected = np.array([[[0.0, 0.25, 0.5, 1.0], [1.0, 0.5, 0.25, 1.0]]], dtype=np.float32)
    images_before = len(bpy.data.images)
    image_settings = scene.render.image_settings
    render_settings_before = (
        image_settings.file_format,
        image_settings.color_mode,
        image_settings.color_depth,
        image_settings.exr_codec,
    )
    image_io.write_rgba(image_path, expected)
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

    object_datamosh.register()
    object_datamosh.unregister()
    assert not hasattr(bpy.types.Scene, "ODM_settings")

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

    print("Object Datamosh Blender smoke test passed")


if __name__ == "__main__":
    main()
