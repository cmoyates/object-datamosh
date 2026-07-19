"""Non-destructive Blender scene creation for manual vector-pass calibration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import bpy
from bpy.types import Object, Scene

from .compositor_setup import setup_object_index_passes
from .core.ownership import mark_owned
from .core.paths import SequencePaths

_SCENE_NAME = "ODM_Vector_Calibration"
_TARGET_NAME = "ODM_Calibration_Rectangle"
_CAMERA_NAME = "ODM_Calibration_Camera"
_MATERIAL_NAME = "ODM_Calibration_Bright"
_WORLD_NAME = "ODM_Calibration_World"
_START_FRAME = 1
_END_FRAME = 8
_START_LOCATION = (-2.0, 0.0, 0.0)
_END_LOCATION = (2.0, 0.0, 0.0)


@dataclass(frozen=True)
class VectorCalibrationScene:
    """Observable result of creating the manual calibration scene."""

    scene: Scene
    target: Object
    camera: Object
    start_location: tuple[float, float, float]
    end_location: tuple[float, float, float]


def _create_bright_material() -> Any:
    material = bpy.data.materials.new(_MATERIAL_NAME)
    mark_owned(cast(Any, material))
    material.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    material.use_nodes = True
    node_tree = material.node_tree
    if node_tree is None:
        raise RuntimeError("Blender did not provide a material node tree")
    node_tree.nodes.clear()
    mark_owned(cast(Any, node_tree))
    output = node_tree.nodes.new("ShaderNodeOutputMaterial")
    output.name = "ODM_Calibration_Material_Output"
    output.label = output.name
    mark_owned(cast(Any, output))
    emission = node_tree.nodes.new("ShaderNodeEmission")
    emission.name = "ODM_Calibration_Emission"
    emission.label = emission.name
    mark_owned(cast(Any, emission))
    cast(Any, emission.inputs["Color"]).default_value = (1.0, 1.0, 1.0, 1.0)
    cast(Any, emission.inputs["Strength"]).default_value = 2.0
    node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return material


def create_vector_calibration_scene(paths: SequencePaths) -> VectorCalibrationScene:
    """Create a separate deterministic scene for manually interpreting vector passes.

    The current scene and its objects are not changed. The returned target moves four Blender
    units along world X over eight frames while an orthographic camera observes it head-on.
    """
    scene = bpy.data.scenes.new(_SCENE_NAME)
    mark_owned(cast(Any, scene))
    mark_owned(cast(Any, scene.collection))
    scene.view_layers[0].name = "ODM_Calibration_View_Layer"
    mark_owned(cast(Any, scene.view_layers[0]))
    scene.frame_start = _START_FRAME
    scene.frame_end = _END_FRAME
    cast(Any, scene.render).engine = "CYCLES"
    scene.render.resolution_x = 256
    scene.render.resolution_y = 256
    scene.render.resolution_percentage = 100
    if hasattr(scene.render, "use_motion_blur"):
        scene.render.use_motion_blur = False
    scene.render.film_transparent = True

    world = bpy.data.worlds.new(_WORLD_NAME)
    mark_owned(cast(Any, world))
    world.color = (0.0, 0.0, 0.0)
    scene.world = world

    mesh = bpy.data.meshes.new(_TARGET_NAME)
    mark_owned(cast(Any, mesh))
    mesh.from_pydata(
        [(-1.0, -0.5, 0.0), (1.0, -0.5, 0.0), (1.0, 0.5, 0.0), (-1.0, 0.5, 0.0)],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update()
    target = bpy.data.objects.new(_TARGET_NAME, mesh)
    mark_owned(cast(Any, target))
    scene.collection.objects.link(target)
    mesh.materials.append(_create_bright_material())
    target.location = _START_LOCATION
    target.keyframe_insert("location", frame=_START_FRAME)
    target.location = _END_LOCATION
    target.keyframe_insert("location", frame=_END_FRAME)
    if target.animation_data is not None and target.animation_data.action is not None:
        action = cast(Any, target.animation_data.action)
        action.name = "ODM_Calibration_Action"
        mark_owned(action)
        fcurves: tuple[Any, ...]
        if hasattr(action, "fcurves"):
            fcurves = tuple(action.fcurves)
        else:
            fcurves = tuple(
                fcurve
                for layer in action.layers
                for strip in layer.strips
                for channelbag in strip.channelbags
                for fcurve in channelbag.fcurves
            )
        for fcurve in fcurves:
            for keyframe in fcurve.keyframe_points:
                keyframe.interpolation = "LINEAR"

    camera_data = bpy.data.cameras.new(_CAMERA_NAME)
    mark_owned(cast(Any, camera_data))
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = 7.0
    camera = bpy.data.objects.new(_CAMERA_NAME, camera_data)
    mark_owned(cast(Any, camera))
    scene.collection.objects.link(camera)
    camera.location = (0.0, 0.0, 10.0)
    scene.camera = camera

    view_layer = scene.view_layers[0]
    setup_object_index_passes(scene, view_layer, target, paths)
    scene.frame_set(_START_FRAME)
    return VectorCalibrationScene(
        scene=scene,
        target=target,
        camera=camera,
        start_location=_START_LOCATION,
        end_location=_END_LOCATION,
    )
