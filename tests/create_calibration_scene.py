"""Background verification for the public vector-calibration scene service."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

import bpy

if not hasattr(bpy, "app"):
    import pytest

    pytest.skip("requires Blender's Python runtime", allow_module_level=True)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from object_datamosh.calibration import create_vector_calibration_scene  # noqa: E402
from object_datamosh.core.ownership import is_owned  # noqa: E402
from object_datamosh.core.paths import SequencePaths  # noqa: E402


def main() -> None:
    original_scene = bpy.context.scene
    assert original_scene is not None
    original_frame = original_scene.frame_current
    original_objects = tuple(original_scene.objects)

    paths = SequencePaths(Path(bpy.app.tempdir) / "ODM_vector_calibration_test")
    calibration = create_vector_calibration_scene(paths)

    assert bpy.context.scene == original_scene
    assert original_scene.frame_current == original_frame
    assert tuple(original_scene.objects) == original_objects
    assert calibration.scene != original_scene
    assert calibration.scene.name.startswith("ODM_")
    assert calibration.target.name.startswith("ODM_")
    assert calibration.camera.name.startswith("ODM_")
    assert calibration.scene.camera == calibration.camera
    assert is_owned(cast(Any, calibration.scene.collection))
    assert calibration.scene.view_layers[0].name.startswith("ODM_")
    assert is_owned(cast(Any, calibration.scene))
    assert is_owned(cast(Any, calibration.target))
    assert is_owned(cast(Any, calibration.camera))
    assert cast(Any, calibration.camera.data).type == "ORTHO"
    assert calibration.scene.render.film_transparent
    assert not calibration.scene.render.use_motion_blur
    assert calibration.scene.world is not None
    world_color = calibration.scene.world.color
    assert (world_color[0], world_color[1], world_color[2]) == (0.0, 0.0, 0.0)
    target_mesh = cast(Any, calibration.target.data)
    assert len(target_mesh.materials) == 1
    material = target_mesh.materials[0]
    material_color = material.diffuse_color
    assert tuple(material_color[index] for index in range(4)) == (1.0, 1.0, 1.0, 1.0)
    assert material.node_tree is not None
    assert all(node.name.startswith("ODM_") for node in material.node_tree.nodes)
    assert all(is_owned(node) for node in material.node_tree.nodes)
    assert calibration.target.animation_data is not None
    action = calibration.target.animation_data.action
    assert action is not None
    assert action.name.startswith("ODM_")
    assert is_owned(cast(Any, action))
    assert calibration.scene.frame_start == 1
    assert calibration.scene.frame_end == 8
    assert calibration.start_location == (-2.0, 0.0, 0.0)
    assert calibration.end_location == (2.0, 0.0, 0.0)

    view_layer = calibration.scene.view_layers[0]
    depsgraph = view_layer.depsgraph
    assert depsgraph is not None
    calibration.scene.frame_set(calibration.scene.frame_start)
    evaluated_target = calibration.target.evaluated_get(depsgraph)
    assert (
        tuple(evaluated_target.location[index] for index in range(3)) == calibration.start_location
    )
    calibration.scene.frame_set(4)
    evaluated_target = calibration.target.evaluated_get(depsgraph)
    assert abs(evaluated_target.location.x - (-2.0 / 7.0)) < 1e-6
    calibration.scene.frame_set(calibration.scene.frame_end)
    evaluated_target = calibration.target.evaluated_get(depsgraph)
    assert tuple(evaluated_target.location[index] for index in range(3)) == calibration.end_location

    assert view_layer.use_pass_vector
    assert view_layer.use_pass_object_index
    assert calibration.target.pass_index > 0
    compositor_tree = calibration.scene.compositing_node_group
    assert compositor_tree is not None
    render_layers = cast(Any, compositor_tree.nodes.get("ODM_Render_Layers"))
    assert render_layers is not None
    assert render_layers.scene == calibration.scene
    output_names = {socket.name for socket in render_layers.outputs}
    assert "Vector" in output_names
    assert "Object Index" in output_names

    print(
        "Vector calibration scene:",
        calibration.scene.name,
        calibration.target.name,
        calibration.camera.name,
        calibration.start_location,
        "->",
        calibration.end_location,
    )


if __name__ == "__main__":
    main()
