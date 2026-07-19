"""Non-destructive Object Index compositor setup for Blender.

This module owns only tagged ``ODM_`` nodes and restores the pass state it changes.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
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
_SETUP_TAG = "object_datamosh_setup"
_SETUP_TAG_VALUE = "object_index"
_NODE_ROLE_TAG = "object_datamosh_node_role"
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
    pass_index_property = cast(Any, target_object.bl_rna.properties["pass_index"])
    maximum = int(pass_index_property.hard_max)
    for candidate in range(1, maximum + 1):
        if candidate not in used:
            return candidate
    raise RuntimeError("No nonzero Object Index is available in this scene")


def _owned_node(nodes: Any, name: str, node_type: str) -> Any:
    node = nodes.get(name)
    if node is not None:
        if (
            not is_owned(node)
            or node.get(_SETUP_TAG) != _SETUP_TAG_VALUE
            or node.get(_NODE_ROLE_TAG) != name
            or node.bl_idname != node_type
        ):
            raise RuntimeError(f"Compositor node name is already in use: {name}")
        return node
    for candidate in nodes:
        if (
            is_owned(candidate)
            and candidate.get(_SETUP_TAG) == _SETUP_TAG_VALUE
            and candidate.get(_NODE_ROLE_TAG) == name
            and candidate.bl_idname == node_type
        ):
            candidate.name = name
            candidate.label = name
            return candidate
    node = nodes.new(node_type)
    node.name = name
    node.label = name
    mark_owned(node)
    node[_SETUP_TAG] = _SETUP_TAG_VALUE
    node[_NODE_ROLE_TAG] = name
    return node


def _setup_frame(tree: Any) -> Any | None:
    """Find the owned state frame by tags, even if a user renamed it."""
    for node in tree.nodes:
        if (
            node.bl_idname == "NodeFrame"
            and is_owned(node)
            and node.get(_SETUP_TAG) == _SETUP_TAG_VALUE
            and node.get(_NODE_ROLE_TAG) == _FRAME_NAME
        ):
            return node
    return None


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
    if scene.objects.get(target_object.name) != target_object:
        raise ValueError("Target object must belong to the configured scene")
    if scene.view_layers.get(view_layer.name) != view_layer:
        raise ValueError("View layer must belong to the configured scene")

    pass_index = _unique_pass_index(scene, target_object)
    tree, tree_created = _compositor_tree(scene)
    nodes = tree.nodes
    frame: Any | None = None
    new_setup = False
    try:
        frame = _owned_node(nodes, _FRAME_NAME, "NodeFrame")
        configured_target = frame.get("target_object")
        if configured_target is not None and configured_target != target_object:
            raise RuntimeError(
                "An Object Index setup already targets another object; "
                "restore it before changing target"
            )
        configured_view_layer = frame.get("view_layer_name")
        if configured_view_layer is not None and configured_view_layer != view_layer.name:
            raise RuntimeError(
                "An Object Index setup already targets another view layer; "
                "restore it before changing view layer"
            )
        new_setup = "original_pass_index" not in frame
        if new_setup:
            frame["target_object"] = target_object
            frame["original_pass_index"] = target_object.pass_index
            frame["view_layer_name"] = view_layer.name
            frame["original_use_pass_vector"] = view_layer.use_pass_vector
            frame["original_use_pass_object_index"] = view_layer.use_pass_object_index
            frame["tree_created"] = tree_created

        # Resolve every owned node before changing scene pass state, so name/type
        # conflicts cannot leave a partially applied setup.
        render_layers = _owned_node(nodes, _RENDER_LAYERS_NAME, "CompositorNodeRLayers")
        id_mask = _owned_node(nodes, _ID_MASK_NAME, "CompositorNodeIDMask")
        beauty = _owned_node(nodes, _BEAUTY_OUTPUT_NAME, "CompositorNodeOutputFile")
        vector = _owned_node(nodes, _VECTOR_OUTPUT_NAME, "CompositorNodeOutputFile")
        matte = _owned_node(nodes, _MATTE_OUTPUT_NAME, "CompositorNodeOutputFile")

        target_object.pass_index = pass_index
        view_layer.use_pass_vector = True
        view_layer.use_pass_object_index = True
        if hasattr(render_layers, "scene"):
            render_layers.scene = scene
        render_layers.layer = view_layer.name
        _socket(id_mask.inputs, "Index").default_value = pass_index

        frame_token = "#" * paths.frame_padding
        _configure_file_output(
            beauty, paths.root / "raw" / "beauty", f"ODM_beauty_{frame_token}", "RGBA"
        )
        _configure_file_output(
            vector, paths.root / "raw" / "vector", f"ODM_vector_{frame_token}", "RGBA"
        )
        _configure_file_output(
            matte, paths.root / "raw" / "matte", f"ODM_matte_{frame_token}", "RGBA"
        )

        for node in (render_layers, id_mask, beauty, vector, matte):
            node.parent = frame

        links = tree.links
        links.new(
            _socket(render_layers.outputs, "Object Index"), _socket(id_mask.inputs, "ID value")
        )
        links.new(_socket(render_layers.outputs, "Image"), _socket(beauty.inputs, "Image"))
        links.new(_socket(render_layers.outputs, "Vector"), _socket(vector.inputs, "Image"))
        links.new(_socket(id_mask.outputs, "Alpha"), _socket(matte.inputs, "Image"))
    except Exception:
        if new_setup and frame is not None:
            restore_object_index_passes(scene)
        elif tree_created and len(tree.nodes) == 0:
            scene.compositing_node_group = None
            bpy.data.node_groups.remove(tree)
        raise

    logging.getLogger(__name__).info(
        "Configured Object Index pass %d for %s on view layer %s; output root=%s",
        pass_index,
        target_object.name,
        view_layer.name,
        paths.root,
    )
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
    return _setup_frame(tree) is not None


@contextmanager
def temporary_raw_output_paths(
    scene: Scene, view_layer: ViewLayer, paths: SequencePaths
) -> Iterator[None]:
    """Point owned raw outputs at ``paths`` and restore their prior paths afterward."""
    tree = (
        scene.compositing_node_group
        if hasattr(scene, "compositing_node_group")
        else cast(Any, scene).node_tree
    )
    if tree is None:
        raise RuntimeError("Set up Object Index passes before rendering raw passes")
    frame = _setup_frame(tree)
    if frame is None:
        raise RuntimeError("Set up Object Index passes before rendering raw passes")
    if frame.get("view_layer_name") != view_layer.name:
        raise RuntimeError(
            "Object Index passes are set up for another view layer; restore and set them up again"
        )

    outputs = (
        (_BEAUTY_OUTPUT_NAME, paths.root / "raw" / "beauty", "ODM_beauty_"),
        (_VECTOR_OUTPUT_NAME, paths.root / "raw" / "vector", "ODM_vector_"),
        (_MATTE_OUTPUT_NAME, paths.root / "raw" / "matte", "ODM_matte_"),
    )
    configured: list[tuple[Any, str, str]] = []
    try:
        for role, directory, prefix in outputs:
            node: Any | None = None
            for candidate in tree.nodes:
                output = cast(Any, candidate)
                if (
                    is_owned(output)
                    and output.get(_SETUP_TAG) == _SETUP_TAG_VALUE
                    and output.get(_NODE_ROLE_TAG) == role
                    and output.bl_idname == "CompositorNodeOutputFile"
                ):
                    node = output
                    break
            if node is None:
                raise RuntimeError(f"Object Index setup is missing its owned output: {role}")
            configured.append((node, node.directory, node.file_name))
            node.directory = str(directory)
            node.file_name = f"{prefix}{'#' * paths.frame_padding}"
        yield
    finally:
        restoration_errors: list[Exception] = []
        for node, directory, file_name in configured:
            try:
                node.directory = directory
            except Exception as error:
                restoration_errors.append(error)
            try:
                node.file_name = file_name
            except Exception as error:
                restoration_errors.append(error)
        if restoration_errors:
            raise restoration_errors[0]


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
    frame = _setup_frame(tree)
    if frame is None:
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
    owned_setup_nodes = [
        node
        for node in tree.nodes
        if is_owned(cast(Any, node)) and node.get(_SETUP_TAG) == _SETUP_TAG_VALUE
    ]
    for node in reversed(owned_setup_nodes):
        tree.nodes.remove(node)

    if tree_created and is_owned(cast(Any, tree)) and len(tree.nodes) == 0:
        if hasattr(scene, "compositing_node_group"):
            scene.compositing_node_group = None
        bpy.data.node_groups.remove(tree)
    logging.getLogger(__name__).info(
        "Restored Object Index setup for target=%s view_layer=%s",
        getattr(target_object, "name", "<deleted>"),
        view_layer_name,
    )
    return True
