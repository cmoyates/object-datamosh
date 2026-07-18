"""Non-destructive Object Index compositor setup for Blender.

This module owns only tagged ``ODM_`` nodes and restores the pass state it changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import bpy
from bpy.types import Object, Scene, ViewLayer

from .core.ownership import is_owned, mark_owned
from .core.paths import SequencePaths

_FRAME_NAME = "ODM_Object_Index_Setup"
_RENDER_LAYERS_NAME = "ODM_Render_Layers"
_ID_MASK_NAME = "ODM_ID_Mask"
_BEAUTY_OUTPUT_NAME = "ODM_Beauty_Output"
_VECTOR_OUTPUT_NAME = "ODM_Vector_Output"
_MATTE_OUTPUT_NAME = "ODM_Matte_Output"
_NODE_NAMES = (
    _FRAME_NAME,
    _RENDER_LAYERS_NAME,
    _ID_MASK_NAME,
    _BEAUTY_OUTPUT_NAME,
    _VECTOR_OUTPUT_NAME,
    _MATTE_OUTPUT_NAME,
)


@dataclass(frozen=True)
class ObjectIndexSetup:
    """Observable result of configuring the Object Index pass seam."""

    pass_index: int
    node_names: tuple[str, ...]


def _compositor_tree(scene: Scene) -> tuple[Any, bool]:
    """Return the scene compositor tree and whether this service created it."""
    if hasattr(scene, "compositing_node_group"):
        tree = scene.compositing_node_group
        if tree is None:
            tree = bpy.data.node_groups.new("ODM_Object_Datamosh_Compositor", "CompositorNodeTree")
            mark_owned(cast(Any, tree))
            scene.compositing_node_group = tree
            return tree, True
        return tree, False

    # Blender before 5.0 exposed the compositor tree through ``node_tree``.
    scene.use_nodes = True
    tree = cast(Any, scene).node_tree
    if tree is None:
        raise RuntimeError("Blender did not provide a compositor node tree")
    return tree, False


def _unique_pass_index(scene: Scene, target_object: Object) -> int:
    used = {obj.pass_index for obj in scene.objects if obj != target_object and obj.pass_index > 0}
    candidate = 1
    while candidate in used:
        candidate += 1
    return candidate


def _owned_node(nodes: Any, name: str, node_type: str) -> Any:
    node = nodes.get(name)
    if node is not None:
        if not is_owned(node) or node.bl_idname != node_type:
            raise RuntimeError(f"Compositor node name is already in use: {name}")
        return node
    node = nodes.new(node_type)
    node.name = name
    node.label = name
    mark_owned(node)
    return node


def _socket(sockets: Any, name: str) -> Any:
    for socket in sockets:
        if socket.name == name or socket.identifier == name:
            return socket
    raise RuntimeError(f"Required compositor socket is unavailable: {name}")


def _configure_file_output(node: Any, directory: Path, file_name: str, socket_type: str) -> None:
    """Configure one Blender 5.0 File Output node for full-float OpenEXR."""
    node.directory = str(directory)
    node.file_name = file_name
    node.format.file_format = "OPEN_EXR_MULTILAYER"
    node.format.color_mode = "RGB" if socket_type == "FLOAT" else "RGBA"
    node.format.color_depth = "32"
    node.format.exr_codec = "ZIP"
    node.save_as_render = False
    node.file_output_items.clear()
    item = node.file_output_items.new(socket_type, "Image")
    item.override_node_format = False
    item.save_as_render = False


def setup_object_index_passes(
    scene: Scene,
    view_layer: ViewLayer,
    target_object: Object,
    paths: SequencePaths,
) -> ObjectIndexSetup:
    """Idempotently configure owned beauty, vector, and Object Index matte outputs."""
    if target_object.name not in scene.objects:
        raise ValueError("Target object must belong to the configured scene")

    tree, tree_created = _compositor_tree(scene)
    nodes = tree.nodes
    frame = _owned_node(nodes, _FRAME_NAME, "NodeFrame")
    configured_target = frame.get("target_object")
    if configured_target is not None and configured_target != target_object:
        raise RuntimeError(
            "An Object Index setup already targets another object; "
            "restore it before changing target"
        )
    if "original_pass_index" not in frame:
        frame["target_object"] = target_object
        frame["original_pass_index"] = target_object.pass_index
        frame["view_layer_name"] = view_layer.name
        frame["original_use_pass_vector"] = view_layer.use_pass_vector
        frame["original_use_pass_object_index"] = view_layer.use_pass_object_index
        frame["tree_created"] = tree_created

    pass_index = _unique_pass_index(scene, target_object)
    target_object.pass_index = pass_index
    view_layer.use_pass_vector = True
    view_layer.use_pass_object_index = True

    render_layers = _owned_node(nodes, _RENDER_LAYERS_NAME, "CompositorNodeRLayers")
    render_layers.layer = view_layer.name
    id_mask = _owned_node(nodes, _ID_MASK_NAME, "CompositorNodeIDMask")
    _socket(id_mask.inputs, "Index").default_value = pass_index

    beauty = _owned_node(nodes, _BEAUTY_OUTPUT_NAME, "CompositorNodeOutputFile")
    vector = _owned_node(nodes, _VECTOR_OUTPUT_NAME, "CompositorNodeOutputFile")
    matte = _owned_node(nodes, _MATTE_OUTPUT_NAME, "CompositorNodeOutputFile")
    frame_token = "#" * paths.frame_padding
    _configure_file_output(
        beauty, paths.root / "raw" / "beauty", f"ODM_beauty_{frame_token}", "RGBA"
    )
    _configure_file_output(
        vector, paths.root / "raw" / "vector", f"ODM_vector_{frame_token}", "RGBA"
    )
    _configure_file_output(matte, paths.root / "raw" / "matte", f"ODM_matte_{frame_token}", "RGBA")

    for node in (render_layers, id_mask, beauty, vector, matte):
        node.parent = frame

    links = tree.links
    links.new(_socket(render_layers.outputs, "Object Index"), _socket(id_mask.inputs, "ID value"))
    links.new(_socket(render_layers.outputs, "Image"), _socket(beauty.inputs, "Image"))
    links.new(_socket(render_layers.outputs, "Vector"), _socket(vector.inputs, "Image"))
    links.new(_socket(id_mask.outputs, "Alpha"), _socket(matte.inputs, "Image"))

    return ObjectIndexSetup(pass_index=pass_index, node_names=_NODE_NAMES)


def has_object_index_setup(scene: Scene) -> bool:
    """Return whether the scene's active compositor tree contains the owned setup."""
    tree = (
        scene.compositing_node_group
        if hasattr(scene, "compositing_node_group")
        else cast(Any, scene).node_tree
    )
    if tree is None:
        return False
    frame = tree.nodes.get(_FRAME_NAME)
    return frame is not None and is_owned(cast(Any, frame))


def restore_object_index_passes(scene: Scene) -> bool:
    """Remove owned setup nodes and restore the target and view-layer pass state.

    Returns ``True`` when an owned setup was restored and ``False`` when no setup exists.
    """
    tree = (
        scene.compositing_node_group
        if hasattr(scene, "compositing_node_group")
        else cast(Any, scene).node_tree
    )
    if tree is None:
        return False
    frame = tree.nodes.get(_FRAME_NAME)
    if frame is None or not is_owned(cast(Any, frame)):
        return False

    target_object = frame.get("target_object")
    if isinstance(target_object, Object):
        target_object.pass_index = int(frame["original_pass_index"])

    view_layer_name = str(frame["view_layer_name"])
    view_layer = scene.view_layers.get(view_layer_name)
    if view_layer is not None:
        view_layer.use_pass_vector = bool(frame["original_use_pass_vector"])
        view_layer.use_pass_object_index = bool(frame["original_use_pass_object_index"])

    tree_created = bool(frame.get("tree_created", False))
    for name in reversed(_NODE_NAMES):
        node = tree.nodes.get(name)
        if node is not None and is_owned(cast(Any, node)):
            tree.nodes.remove(node)

    if tree_created and is_owned(cast(Any, tree)) and len(tree.nodes) == 0:
        if hasattr(scene, "compositing_node_group"):
            scene.compositing_node_group = None
        bpy.data.node_groups.remove(tree)
    return True
