"""Blender registration, properties, operators, and sidebar UI.

``bpy`` is supplied by Blender and is unavailable to the repository's normal Python runtime.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import (
    Context,
    Operator,
    Panel,
    PropertyGroup,
    Scene,
)

from .core.contracts import FeedbackSettings, MatteSource, MotionChannels
from .core.paths import SequencePaths


def settings_for_scene(scene: Scene) -> ODM_Settings:
    """Return the dynamically registered settings attached to ``scene``."""
    return cast(ODM_Settings, cast(Any, scene).object_datamosh)


def feedback_settings_for_scene(scene: Scene) -> FeedbackSettings:
    """Copy Blender scene properties into the pure processing contract."""
    settings = settings_for_scene(scene)
    return FeedbackSettings(
        persistence=settings.persistence,
        block_size=settings.block_size,
        motion_channels=MotionChannels(settings.motion_channels),
        reverse_motion=settings.reverse_motion,
        flip_x=settings.flip_x,
        flip_y=settings.flip_y,
        motion_gain=settings.motion_gain,
        motion_clamp=settings.motion_clamp,
        motion_quantization=settings.motion_quantization,
        diffusion=settings.diffusion,
        refresh_probability=settings.refresh_probability,
        seed=settings.seed,
        matte_source=MatteSource(settings.matte_source),
    )


def sequence_paths_for_scene(scene: Scene) -> SequencePaths:
    """Derive safe sequence paths from Blender's current file state."""
    settings = settings_for_scene(scene)
    if settings.output_directory:
        return SequencePaths(root=Path(bpy.path.abspath(settings.output_directory)))
    return SequencePaths.from_blend_file(
        bpy.data.filepath,
        temp_directory=bpy.app.tempdir,
    )


class ODM_Settings(PropertyGroup):
    """Scene-owned Object Datamosh settings; no module-level runtime state."""

    target_object: PointerProperty(  # ty: ignore[invalid-type-form]
        name="Target Object", type=bpy.types.Object
    )
    frame_start: IntProperty(name="Start", default=1)  # ty: ignore[invalid-type-form]
    frame_end: IntProperty(name="End", default=250)  # ty: ignore[invalid-type-form]
    output_directory: StringProperty(  # ty: ignore[invalid-type-form]
        name="Output Directory",
        description="Leave empty to derive a directory beside the saved blend file",
        subtype="DIR_PATH",
        default="",
    )
    matte_source: EnumProperty(  # ty: ignore[invalid-type-form]
        name="Matte Source",
        items=(
            ("OBJECT_INDEX", "Object Index", "Use the selected object's Object Index pass"),
            ("EXTERNAL", "External Matte", "Use a numbered external matte sequence"),
            (
                "CRYPTOMATTE",
                "Cryptomatte (Experimental)",
                "Reserved contract; decoding is not implemented in the MVP",
            ),
        ),
        default="OBJECT_INDEX",
    )
    external_matte_directory: StringProperty(  # ty: ignore[invalid-type-form]
        name="Matte Directory", subtype="DIR_PATH", default=""
    )
    persistence: FloatProperty(  # ty: ignore[invalid-type-form]
        name="Persistence", default=0.85, min=0.0, max=1.0
    )
    block_size: IntProperty(  # ty: ignore[invalid-type-form]
        name="Block Size", default=16, min=1
    )
    motion_channels: EnumProperty(  # ty: ignore[invalid-type-form]
        name="Motion Channels",
        items=(
            ("RG", "RG", "Read X/Y motion from red and green"),
            ("BA", "BA", "Read X/Y motion from blue and alpha"),
        ),
        default="RG",
    )
    reverse_motion: BoolProperty(  # ty: ignore[invalid-type-form]
        name="Reverse Motion", default=False
    )
    flip_x: BoolProperty(name="Flip X", default=False)  # ty: ignore[invalid-type-form]
    flip_y: BoolProperty(name="Flip Y", default=False)  # ty: ignore[invalid-type-form]
    motion_gain: FloatProperty(  # ty: ignore[invalid-type-form]
        name="Motion Gain", default=1.0, min=0.0
    )
    motion_clamp: FloatProperty(  # ty: ignore[invalid-type-form]
        name="Motion Clamp", default=64.0, min=0.0
    )
    motion_quantization: FloatProperty(  # ty: ignore[invalid-type-form]
        name="Motion Quantization", default=1.0, min=0.0
    )
    diffusion: FloatProperty(  # ty: ignore[invalid-type-form]
        name="Diffusion", default=0.0, min=0.0
    )
    refresh_probability: FloatProperty(  # ty: ignore[invalid-type-form]
        name="Refresh Probability", default=0.0, min=0.0, max=1.0
    )
    seed: IntProperty(name="Seed", default=0)  # ty: ignore[invalid-type-form]
    status: StringProperty(  # ty: ignore[invalid-type-form]
        name="Status", default="Ready"
    )


class ODM_OT_use_active_object(Operator):
    """Assign the active object as the datamosh target."""

    bl_idname = "object_datamosh.use_active_object"
    bl_label = "Use Active Object"
    bl_description = "Assign the active object as the Object Datamosh target"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return context.scene is not None and context.active_object is not None

    def execute(self, context: Context) -> set[Any]:
        scene = context.scene
        active_object = context.active_object
        if scene is None or active_object is None:
            self.report({"ERROR"}, "An active object is required")
            return {"CANCELLED"}
        settings_for_scene(scene).target_object = active_object
        self.report({"INFO"}, f"Target set to {active_object.name}")
        return {"FINISHED"}


class ODM_PT_sidebar(Panel):
    """Object Datamosh controls in the 3D View sidebar."""

    bl_label = "Object Datamosh"
    bl_idname = "ODM_PT_sidebar"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Object Datamosh"

    def draw(self, context: Context) -> None:
        layout = self.layout
        scene = context.scene
        if layout is None or scene is None:
            return
        settings = settings_for_scene(scene)
        paths = sequence_paths_for_scene(scene)

        target = layout.box()
        target.label(text="Target")
        target.prop(settings, "target_object")
        target.operator(ODM_OT_use_active_object.bl_idname)
        view_layer_name = context.view_layer.name if context.view_layer is not None else "None"
        target.label(text=f"View Layer: {view_layer_name}")

        sequence = layout.box()
        sequence.label(text="Sequence")
        row = sequence.row(align=True)
        row.prop(settings, "frame_start")
        row.prop(settings, "frame_end")
        sequence.prop(settings, "output_directory")
        sequence.label(text=f"Output: {paths.root}")
        if paths.warning:
            warning = sequence.row()
            warning.alert = True
            warning.label(text=paths.warning, icon="ERROR")

        matte = layout.box()
        matte.label(text="Matte")
        matte.prop(settings, "matte_source")
        if settings.matte_source == "EXTERNAL":
            matte.prop(settings, "external_matte_directory")
        elif settings.matte_source == "CRYPTOMATTE":
            matte.label(text="Experimental; decoding is not yet available", icon="INFO")

        feedback = layout.box()
        feedback.label(text="Feedback")
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

        layout.label(text=f"Status: {settings.status}")


_CLASSES = (ODM_Settings, ODM_OT_use_active_object, ODM_PT_sidebar)


def register() -> None:
    """Register classes and scene properties idempotently."""
    for cls in _CLASSES:
        if not getattr(cls, "is_registered", False):
            bpy.utils.register_class(cls)
    scene_type = cast(Any, Scene)
    if not hasattr(scene_type, "object_datamosh"):
        scene_type.object_datamosh = PointerProperty(type=ODM_Settings)


def unregister() -> None:
    """Remove only data registered by this extension, idempotently."""
    scene_type = cast(Any, Scene)
    if hasattr(scene_type, "object_datamosh"):
        del scene_type.object_datamosh
    for cls in reversed(_CLASSES):
        if getattr(cls, "is_registered", False):
            bpy.utils.unregister_class(cls)
