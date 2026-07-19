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

from .blender_image_io import BlenderImageIO
from .calibration import create_vector_calibration_scene
from .compositor_setup import (
    has_object_index_setup,
    restore_object_index_passes,
    setup_object_index_passes,
)
from .core.contracts import FeedbackSettings, MatteSource, MotionChannels
from .core.mattes import (
    CryptomatteMatteProvider,
    ExternalMatteProvider,
    ObjectIndexMatteProvider,
)
from .core.paths import SequencePaths
from .raw_render import RawRenderCancelled, render_raw_passes
from .sequence_processing import SequenceProcessingCancelled, process_sequence

_SCENE_SETTINGS_ATTRIBUTE = "ODM_settings"


def settings_for_scene(scene: Scene) -> ODM_Settings:
    """Return the dynamically registered settings attached to ``scene``."""
    return cast(ODM_Settings, getattr(scene, _SCENE_SETTINGS_ATTRIBUTE))


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
        output_root = Path(bpy.path.abspath(settings.output_directory))
        if output_root.is_absolute():
            warning = None
            if not bpy.data.filepath:
                warning = "Blend file is unsaved; using the explicit absolute output directory."
            return SequencePaths(root=output_root, warning=warning)
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
        options={"PATH_SUPPORTS_BLEND_RELATIVE"},
        default="",
    )
    overwrite_raw: BoolProperty(  # ty: ignore[invalid-type-form]
        name="Overwrite Raw Passes",
        description="Allow Render Raw Passes to replace files for the configured frame range",
        default=False,
    )
    overwrite_processed: BoolProperty(  # ty: ignore[invalid-type-form]
        name="Overwrite Processed Frames",
        description="Allow processing to replace files for the configured frame range",
        default=False,
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
            message = "An active object is required"
            if scene is not None:
                settings_for_scene(scene).status = message
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        settings = settings_for_scene(scene)
        settings.target_object = active_object
        message = f"Target set to {active_object.name}"
        settings.status = message
        self.report({"INFO"}, message)
        return {"FINISHED"}


class ODM_OT_setup_object_index(Operator):
    """Configure owned compositor outputs for the selected target object."""

    bl_idname = "object_datamosh.setup_object_index"
    bl_label = "Setup Object Index"
    bl_description = "Configure non-destructive Object Index, vector, and beauty outputs"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context) -> bool:
        if context.scene is None or context.view_layer is None:
            return False
        return settings_for_scene(context.scene).target_object is not None

    def execute(self, context: Context) -> set[Any]:
        scene = context.scene
        view_layer = context.view_layer
        if scene is None or view_layer is None:
            self.report({"ERROR"}, "An active scene and view layer are required")
            return {"CANCELLED"}
        settings = settings_for_scene(scene)
        target_object = settings.target_object
        if target_object is None:
            message = "Choose a target object before setting up Object Index passes"
            settings.status = message
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        try:
            setup = setup_object_index_passes(
                scene,
                view_layer,
                target_object,
                sequence_paths_for_scene(scene),
            )
        except (RuntimeError, TypeError, ValueError) as error:
            settings.status = str(error)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        message = f"Object Index setup ready (pass {setup.pass_index})"
        settings.status = message
        self.report({"INFO"}, message)
        return {"FINISHED"}


class _WindowManagerProgress:
    """Adapt Blender's window-manager progress API to the raw-render service."""

    def __init__(self, window_manager: Any) -> None:
        self._window_manager = window_manager

    def begin(self, total: int) -> None:
        self._window_manager.progress_begin(0, total)

    def update(self, completed: int) -> None:
        self._window_manager.progress_update(completed)

    def end(self) -> None:
        self._window_manager.progress_end()


class ODM_OT_render_raw_passes(Operator):
    """Render the configured frame range to separate raw EXR pass sequences."""

    bl_idname = "object_datamosh.render_raw_passes"
    bl_label = "Render Raw Passes"
    bl_description = "Render beauty, vector, and Object Index matte EXR sequences"

    @classmethod
    def poll(cls, context: Context) -> bool:
        if context.scene is None or context.view_layer is None:
            return False
        settings = settings_for_scene(context.scene)
        return (
            settings.target_object is not None
            and settings.frame_start <= settings.frame_end
            and has_object_index_setup(context.scene)
        )

    def execute(self, context: Context) -> set[Any]:
        scene = context.scene
        view_layer = context.view_layer
        if scene is None or view_layer is None:
            self.report({"ERROR"}, "An active scene and view layer are required")
            return {"CANCELLED"}
        settings = settings_for_scene(scene)
        settings.status = "Rendering raw passes..."
        try:
            result = render_raw_passes(
                scene,
                view_layer,
                sequence_paths_for_scene(scene),
                frame_start=settings.frame_start,
                frame_end=settings.frame_end,
                overwrite=settings.overwrite_raw,
                progress=_WindowManagerProgress(context.window_manager),
            )
        except RawRenderCancelled as error:
            message = str(error)
            settings.status = message
            self.report({"WARNING"}, message)
            return {"CANCELLED"}
        except (FileExistsError, RuntimeError, TypeError, ValueError) as error:
            message = str(error)
            settings.status = message
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        message = f"Rendered {len(result.frames)} raw frame(s)"
        settings.status = message
        self.report({"INFO"}, message)
        return {"FINISHED"}


class ODM_OT_process_sequence(Operator):
    """Process existing pass files through hard-localized feedback."""

    bl_idname = "object_datamosh.process_sequence"
    bl_label = "Process Existing Passes"
    bl_description = "Process existing beauty, vector, and matte EXR sequences"

    @classmethod
    def poll(cls, context: Context) -> bool:
        if context.scene is None:
            return False
        settings = settings_for_scene(context.scene)
        return settings.frame_start <= settings.frame_end

    def execute(self, context: Context) -> set[Any]:
        scene = context.scene
        if scene is None:
            self.report({"ERROR"}, "An active scene is required")
            return {"CANCELLED"}
        settings = settings_for_scene(scene)
        if settings.matte_source == MatteSource.EXTERNAL:
            if not settings.external_matte_directory:
                message = "Choose an external matte directory before processing"
                settings.status = message
                self.report({"ERROR"}, message)
                return {"CANCELLED"}
            matte_provider = ExternalMatteProvider(
                Path(bpy.path.abspath(settings.external_matte_directory))
            )
        elif settings.matte_source == MatteSource.CRYPTOMATTE:
            matte_provider = CryptomatteMatteProvider()
        else:
            matte_provider = ObjectIndexMatteProvider()

        settings.status = "Processing existing passes..."
        try:
            result = process_sequence(
                sequence_paths_for_scene(scene),
                frame_start=settings.frame_start,
                frame_end=settings.frame_end,
                matte_provider=matte_provider,
                settings=feedback_settings_for_scene(scene),
                image_io=BlenderImageIO(),
                overwrite=settings.overwrite_processed,
                progress=_WindowManagerProgress(context.window_manager),
            )
        except SequenceProcessingCancelled as error:
            message = str(error)
            settings.status = message
            self.report({"WARNING"}, message)
            return {"CANCELLED"}
        except (
            NotImplementedError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            message = str(error)
            settings.status = message
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        message = f"Processed {len(result.frames)} frame(s)"
        settings.status = message
        self.report({"INFO"}, message)
        return {"FINISHED"}


class ODM_OT_create_vector_calibration(Operator):
    """Create a separate deterministic scene for manual vector calibration."""

    bl_idname = "object_datamosh.create_vector_calibration"
    bl_label = "Create Vector Calibration Scene"
    bl_description = "Create a separate animated ODM_ scene for interpreting vector passes"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return context.scene is not None

    def execute(self, context: Context) -> set[Any]:
        scene = context.scene
        if scene is None:
            self.report({"ERROR"}, "An active scene is required")
            return {"CANCELLED"}
        settings = settings_for_scene(scene)
        try:
            calibration = create_vector_calibration_scene(sequence_paths_for_scene(scene))
        except (RuntimeError, TypeError, ValueError) as error:
            message = str(error)
            settings.status = message
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        calibration_settings = settings_for_scene(calibration.scene)
        calibration_settings.target_object = calibration.target
        calibration_settings.frame_start = calibration.scene.frame_start
        calibration_settings.frame_end = calibration.scene.frame_end
        calibration_settings.output_directory = settings.output_directory
        message = f"Created {calibration.scene.name} (frames 1-8)"
        settings.status = message
        self.report({"INFO"}, message)
        return {"FINISHED"}


class ODM_OT_restore_object_index(Operator):
    """Remove owned compositor setup and restore changed pass settings."""

    bl_idname = "object_datamosh.restore_object_index"
    bl_label = "Restore Object Index Setup"
    bl_description = "Remove Object Datamosh nodes and restore prior pass settings"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return context.scene is not None and has_object_index_setup(context.scene)

    def execute(self, context: Context) -> set[Any]:
        scene = context.scene
        if scene is None:
            self.report({"ERROR"}, "An active scene is required")
            return {"CANCELLED"}
        settings = settings_for_scene(scene)
        if not restore_object_index_passes(scene):
            message = "No Object Datamosh Object Index setup was found"
            settings.status = message
            self.report({"WARNING"}, message)
            return {"CANCELLED"}
        message = "Object Index setup restored"
        settings.status = message
        self.report({"INFO"}, message)
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
        _draw_sidebar(layout, context, scene)


def _draw_sidebar(layout: Any, context: Context, scene: Scene) -> None:
    """Emit the complete sidebar surface through Blender's layout interface."""
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
    sequence.prop(settings, "overwrite_raw")
    sequence.operator(ODM_OT_render_raw_passes.bl_idname)
    sequence.prop(settings, "overwrite_processed")
    sequence.operator(ODM_OT_process_sequence.bl_idname)
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
    else:
        row = matte.row(align=True)
        row.operator(ODM_OT_setup_object_index.bl_idname)
        row.operator(ODM_OT_restore_object_index.bl_idname)

    calibration = layout.box()
    calibration.label(text="Vector Calibration")
    calibration.operator(ODM_OT_create_vector_calibration.bl_idname)

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


_CLASSES = (
    ODM_Settings,
    ODM_OT_use_active_object,
    ODM_OT_setup_object_index,
    ODM_OT_render_raw_passes,
    ODM_OT_process_sequence,
    ODM_OT_create_vector_calibration,
    ODM_OT_restore_object_index,
    ODM_PT_sidebar,
)


def _owns_scene_settings_property() -> bool:
    scene_type = cast(Any, Scene)
    deferred_property = getattr(scene_type, _SCENE_SETTINGS_ATTRIBUTE, None)
    keywords = getattr(deferred_property, "keywords", {})
    return keywords.get("type") is ODM_Settings


def register() -> None:
    """Register classes and the owned scene property idempotently."""
    scene_type = cast(Any, Scene)
    if hasattr(scene_type, _SCENE_SETTINGS_ATTRIBUTE) and not _owns_scene_settings_property():
        raise RuntimeError(
            f"Scene.{_SCENE_SETTINGS_ATTRIBUTE} already exists and is not owned by Object Datamosh"
        )

    for cls in _CLASSES:
        if not getattr(cls, "is_registered", False):
            bpy.utils.register_class(cls)
    if not hasattr(scene_type, _SCENE_SETTINGS_ATTRIBUTE):
        setattr(
            scene_type,
            _SCENE_SETTINGS_ATTRIBUTE,
            PointerProperty(type=ODM_Settings),
        )


def unregister() -> None:
    """Remove only data registered by this extension, idempotently."""
    scene_type = cast(Any, Scene)
    if _owns_scene_settings_property():
        delattr(scene_type, _SCENE_SETTINGS_ATTRIBUTE)
    for cls in reversed(_CLASSES):
        if getattr(cls, "is_registered", False):
            bpy.utils.unregister_class(cls)
