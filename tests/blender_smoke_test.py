"""Blender background smoke test for the extension's public registration seam."""

from __future__ import annotations

import shutil
import sys
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
from object_datamosh.blender_image_io import BlenderImageIO  # noqa: E402
from object_datamosh.core.contracts import FeedbackSettings  # noqa: E402
from object_datamosh.ui import (  # noqa: E402
    _draw_sidebar,
    feedback_settings_for_scene,
    sequence_paths_for_scene,
    settings_for_scene,
)


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
    assert layout.operators == {"object_datamosh.use_active_object"}
    assert any(label.startswith("View Layer: ") for label in layout.labels)
    assert any(label.startswith("Output: ") for label in layout.labels)
    assert any(label.startswith("Status: ") for label in layout.labels)
    assert "Save the blend file to use a project-relative output directory." in layout.labels
    settings.matte_source = "OBJECT_INDEX"

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
    assert custom_paths.warning is None

    saved_blend = Path(bpy.app.tempdir) / "ODM_smoke.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(saved_blend))
    settings.output_directory = "//ODM_relative_output"
    saved_relative_paths = sequence_paths_for_scene(scene)
    assert saved_relative_paths.root == saved_blend.parent / "ODM_relative_output"
    assert saved_relative_paths.warning is None

    image_io = BlenderImageIO()
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
