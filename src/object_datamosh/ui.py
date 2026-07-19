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
from .blender_render_adapter import BlenderRenderAdapter
from .calibration import create_vector_calibration_scene
from .compositor_setup import (
    has_object_index_setup,
    restore_object_index_passes,
    setup_object_index_passes,
)
from .core.contracts import FeedbackMode, FeedbackSettings, MatteSource, MotionChannels
from .core.mattes import (
    CryptomatteMatteProvider,
    ExternalMatteProvider,
    ObjectIndexMatteProvider,
)
from .core.paths import SequencePaths
from .existing_pass_operation import ExistingPassModalController
from .modal_lifecycle import OperationPhase, request_cancellation
from .orchestration import RenderAndProcessPhase, render_and_process
from .raw_render import RawRenderCancelled, RawRenderSession, render_raw_passes
from .raw_render_operation import RawRenderModalController
from .render_and_process_operation import RenderAndProcessModalController
from .sequence_processing import (
    MissingHistoryPolicy,
    ProcessingSession,
    ResolutionChangePolicy,
    SequenceProcessingCancelled,
    SequenceRunMode,
    parse_reset_frames,
    process_sequence,
)
from .sidebar import draw_sidebar

_SCENE_SETTINGS_ATTRIBUTE = "ODM_settings"
_SCENE_RUNTIME_ATTRIBUTE = "ODM_runtime"
_ACTIVE_CONTROLLER_KEY = "ODM_active_modal_controller"


def _driver_namespace() -> dict[str, object]:
    return cast(dict[str, object], cast(Any, bpy.app).driver_namespace)


def _active_modal_controller() -> (
    ExistingPassModalController | RawRenderModalController | RenderAndProcessModalController | None
):
    controller = _driver_namespace().get(_ACTIVE_CONTROLLER_KEY)
    return (
        controller
        if isinstance(
            controller,
            (
                ExistingPassModalController,
                RawRenderModalController,
                RenderAndProcessModalController,
            ),
        )
        else None
    )


def _clear_active_modal_controller() -> None:
    _driver_namespace().pop(_ACTIVE_CONTROLLER_KEY, None)


def settings_for_scene(scene: Scene) -> ODM_Settings:
    """Return the dynamically registered settings attached to ``scene``."""
    return cast(ODM_Settings, getattr(scene, _SCENE_SETTINGS_ATTRIBUTE))


def runtime_for_scene(scene: Scene) -> ODM_RuntimeState:
    """Return the serializable runtime state owned by ``scene``."""
    return cast(ODM_RuntimeState, getattr(scene, _SCENE_RUNTIME_ATTRIBUTE))


def feedback_settings_for_scene(scene: Scene) -> FeedbackSettings:
    """Copy Blender scene properties into the pure processing contract."""
    settings = settings_for_scene(scene)
    return FeedbackSettings(
        mode=FeedbackMode(settings.feedback_mode),
        trail_decay=settings.trail_decay,
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


class ODM_RuntimeState(PropertyGroup):
    """Transient scene-owned state for one Object Datamosh operation."""

    active: BoolProperty(  # ty: ignore[invalid-type-form]
        name="Active", default=False, options={"SKIP_SAVE"}
    )
    cancel_requested: BoolProperty(  # ty: ignore[invalid-type-form]
        name="Cancel Requested", default=False, options={"SKIP_SAVE"}
    )
    phase: EnumProperty(  # ty: ignore[invalid-type-form]
        name="Phase",
        items=tuple((phase.value, phase.value.title(), "") for phase in OperationPhase),
        default=OperationPhase.IDLE.value,
        options={"SKIP_SAVE"},
    )
    run_identity: StringProperty(  # ty: ignore[invalid-type-form]
        name="Run Identity", default="", options={"SKIP_SAVE"}
    )
    current_frame: IntProperty(  # ty: ignore[invalid-type-form]
        name="Current Frame", default=0, options={"SKIP_SAVE"}
    )
    frame_start: IntProperty(  # ty: ignore[invalid-type-form]
        name="Start", default=0, options={"SKIP_SAVE"}
    )
    frame_end: IntProperty(  # ty: ignore[invalid-type-form]
        name="End", default=0, options={"SKIP_SAVE"}
    )
    completed_work: IntProperty(  # ty: ignore[invalid-type-form]
        name="Completed", default=0, min=0, options={"SKIP_SAVE"}
    )
    total_work: IntProperty(  # ty: ignore[invalid-type-form]
        name="Total", default=0, min=0, options={"SKIP_SAVE"}
    )
    phase_completed_work: IntProperty(  # ty: ignore[invalid-type-form]
        name="Phase Completed", default=0, min=0, options={"SKIP_SAVE"}
    )
    phase_total_work: IntProperty(  # ty: ignore[invalid-type-form]
        name="Phase Total", default=0, min=0, options={"SKIP_SAVE"}
    )
    progress: FloatProperty(  # ty: ignore[invalid-type-form]
        name="Progress",
        default=0.0,
        min=0.0,
        max=1.0,
        subtype="PERCENTAGE",
        options={"SKIP_SAVE"},
    )
    status: StringProperty(  # ty: ignore[invalid-type-form]
        name="Status", default="Ready", options={"SKIP_SAVE"}
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
        description="Allow Reprocess to replace the complete configured frame range",
        default=False,
    )
    sequence_run_mode: EnumProperty(  # ty: ignore[invalid-type-form]
        name="Run Mode",
        items=(
            ("REPROCESS", "Reprocess", "Start at the first frame and replace the complete range"),
            ("RESUME", "Resume", "Continue only from a compatible recovery manifest"),
        ),
        default="REPROCESS",
    )
    reset_frames: StringProperty(  # ty: ignore[invalid-type-form]
        name="Reset Frames",
        description="Comma-separated frames that initialize clean history",
        default="",
    )
    missing_history: EnumProperty(  # ty: ignore[invalid-type-form]
        name="Missing History",
        items=(
            ("ERROR", "Stop", "Stop when recorded resume history is missing or invalid"),
            ("RESET", "Reset", "Reprocess from missing or invalid history with a clean reset"),
        ),
        default="ERROR",
    )
    resolution_change: EnumProperty(  # ty: ignore[invalid-type-form]
        name="Resolution Change",
        items=(
            ("ERROR", "Stop", "Stop before reusing history with different dimensions"),
            ("RESET", "Reset", "Initialize clean history when dimensions change"),
        ),
        default="ERROR",
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
    feedback_mode: EnumProperty(  # ty: ignore[invalid-type-form]
        name="Mode",
        items=(
            (
                "HARD_LOCALIZED",
                "Hard Localized",
                "Keep feedback inside the selected object's current silhouette",
            ),
            (
                "TRAIL",
                "Trail",
                "Retain decayed feedback where selected-object history remains reachable",
            ),
        ),
        default="HARD_LOCALIZED",
    )
    trail_decay: FloatProperty(  # ty: ignore[invalid-type-form]
        name="Trail Decay",
        description="Selected-object trail coverage retained per frame",
        default=0.85,
        min=0.0,
        max=1.0,
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


def _active_operation_runtime() -> ODM_RuntimeState | None:
    """Return the scene-owned runtime holding the process-wide operation lock, if any."""
    for scene in bpy.data.scenes:
        if hasattr(scene, _SCENE_RUNTIME_ATTRIBUTE):
            runtime = runtime_for_scene(scene)
            if runtime.active:
                return runtime
    return None


def _operation_is_idle(context: Context) -> bool:
    return (
        context.scene is not None
        and _active_modal_controller() is None
        and _active_operation_runtime() is None
    )


class ODM_OT_cancel_operation(Operator):
    """Request cancellation; the active workflow stops only at its next safe boundary."""

    bl_idname = "object_datamosh.cancel_operation"
    bl_label = "Cancel"
    bl_description = "Request cancellation at the next safe frame boundary"

    @classmethod
    def poll(cls, context: Context) -> bool:
        return context.scene is not None and (
            _active_modal_controller() is not None or _active_operation_runtime() is not None
        )

    def execute(self, context: Context) -> set[Any]:
        controller = _active_modal_controller()
        if controller is not None:
            cancellation_requested = controller.request_cancel()
            runtime = _active_operation_runtime()
        else:
            runtime = _active_operation_runtime()
            cancellation_requested = runtime is not None and request_cancellation(
                runtime, context.window_manager
            )
        if not cancellation_requested:
            self.report({"WARNING"}, "No cancellable Object Datamosh operation is active")
            return {"CANCELLED"}
        if runtime is None:
            self.report({"INFO"}, "Cancel requested; waiting for a safe boundary...")
            return {"FINISHED"}
        if not runtime.cancel_requested:
            self.report({"WARNING"}, "No cancellable Object Datamosh operation is active")
            return {"CANCELLED"}
        self.report({"INFO"}, runtime.status)
        return {"FINISHED"}


class ODM_OT_use_active_object(Operator):
    """Assign the active object as the datamosh target."""

    bl_idname = "object_datamosh.use_active_object"
    bl_label = "Use Active Object"
    bl_description = "Assign the active object as the Object Datamosh target"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return _operation_is_idle(context) and context.active_object is not None

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
        return (
            _operation_is_idle(context)
            and settings_for_scene(context.scene).target_object is not None
        )

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
    """Render the configured frame range through one modal frame boundary at a time."""

    bl_idname = "object_datamosh.render_raw_passes"
    bl_label = "Render Raw Passes"
    bl_description = "Render beauty, vector, and Object Index matte EXR sequences"

    _controller: RawRenderModalController | None

    @classmethod
    def poll(cls, context: Context) -> bool:
        if context.scene is None or context.view_layer is None:
            return False
        settings = settings_for_scene(context.scene)
        return (
            _operation_is_idle(context)
            and settings.target_object is not None
            and settings.frame_start <= settings.frame_end
            and has_object_index_setup(context.scene)
        )

    def invoke(self, context: Context, event: Any) -> set[Any]:
        del event
        return self.execute(context)

    def execute(self, context: Context) -> set[Any]:
        scene = context.scene
        view_layer = context.view_layer
        if scene is None or view_layer is None:
            self.report({"ERROR"}, "An active scene and view layer are required")
            return {"CANCELLED"}
        settings = settings_for_scene(scene)
        if bpy.app.background and isinstance(context.window_manager, bpy.types.WindowManager):
            # Registered background operators have no window event loop to deliver modal timers.
            # Deterministic smoke harnesses provide a recorder instead and exercise modal startup.
            progress = _WindowManagerProgress(context.window_manager)
            settings.status = "Rendering raw passes in background mode..."
            try:
                result = render_raw_passes(
                    scene,
                    view_layer,
                    sequence_paths_for_scene(scene),
                    frame_start=settings.frame_start,
                    frame_end=settings.frame_end,
                    overwrite=settings.overwrite_raw,
                    progress=progress,
                )
            except RawRenderCancelled as error:
                message = f"Raw rendering cancelled during background rendering: {error}"
                settings.status = message
                self.report({"WARNING"}, message)
                return {"CANCELLED"}
            except Exception as error:
                message = f"Raw rendering failed during background rendering: {error}"
                settings.status = message
                self.report({"ERROR"}, message)
                return {"CANCELLED"}
            message = f"Rendered {len(result.frames)} raw frame(s)"
            settings.status = message
            self.report({"INFO"}, message)
            return {"FINISHED"}

        runtime = runtime_for_scene(scene)
        controller = RawRenderModalController(
            self,
            runtime,
            settings,
            adapter=BlenderRenderAdapter(runtime),
            on_cleanup=_clear_active_modal_controller,
        )
        self._controller = controller
        _driver_namespace()[_ACTIVE_CONTROLLER_KEY] = controller
        settings.status = "Rendering raw passes..."
        try:
            session = RawRenderSession.create(
                scene,
                view_layer,
                sequence_paths_for_scene(scene),
                frame_start=settings.frame_start,
                frame_end=settings.frame_end,
                overwrite=settings.overwrite_raw,
            )
            controller.start(context, session)
        except Exception as error:
            controller.fail_initialization(settings.frame_start, error)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def modal(self, context: Context, event: Any) -> set[Any]:
        controller = self._controller
        if controller is None:
            self.report({"ERROR"}, "Raw rendering failed: the modal controller is unavailable")
            return {"CANCELLED"}
        return controller.handle_event(event)

    def cancel(self, context: Context) -> None:
        if self._controller is not None:
            self._controller.cancel()


def _matte_provider_for_settings(settings: ODM_Settings):
    """Build the configured matte provider at the Blender boundary."""
    if settings.matte_source == MatteSource.EXTERNAL:
        if not settings.external_matte_directory:
            raise ValueError("Choose an external matte directory before processing")
        return ExternalMatteProvider(Path(bpy.path.abspath(settings.external_matte_directory)))
    if settings.matte_source == MatteSource.CRYPTOMATTE:
        return CryptomatteMatteProvider()
    return ObjectIndexMatteProvider()


class ODM_OT_render_and_process(Operator):
    """Render and process the configured range through one modal lifecycle."""

    bl_idname = "object_datamosh.render_and_process"
    bl_label = "Render and Process"
    bl_description = "Render raw passes, then process exactly the completed rendered range"

    _controller: RenderAndProcessModalController | None

    @classmethod
    def poll(cls, context: Context) -> bool:
        if context.scene is None or context.view_layer is None:
            return False
        settings = settings_for_scene(context.scene)
        return (
            _operation_is_idle(context)
            and settings.target_object is not None
            and settings.frame_start <= settings.frame_end
            and has_object_index_setup(context.scene)
        )

    def invoke(self, context: Context, event: Any) -> set[Any]:
        del event
        return self.execute(context)

    def execute(self, context: Context) -> set[Any]:
        scene = context.scene
        view_layer = context.view_layer
        if scene is None or view_layer is None:
            self.report({"ERROR"}, "An active scene and view layer are required")
            return {"CANCELLED"}
        settings = settings_for_scene(scene)
        paths = sequence_paths_for_scene(scene)
        frame_start = settings.frame_start
        frame_end = settings.frame_end
        overwrite_raw = settings.overwrite_raw
        overwrite_processed = settings.overwrite_processed
        reset_frames_text = settings.reset_frames
        resolution_change_value = settings.resolution_change
        missing_history_value = settings.missing_history
        if not (bpy.app.background and isinstance(context.window_manager, bpy.types.WindowManager)):
            runtime = runtime_for_scene(scene)

            def create_processing(input_frames, should_cancel):
                return ProcessingSession.create(
                    paths,
                    frame_start=frame_start,
                    frame_end=frame_end,
                    matte_provider=matte_provider,
                    settings=feedback_settings,
                    image_io=image_io,
                    overwrite=overwrite_processed,
                    reset_frames=reset_frames,
                    resolution_change=resolution_change,
                    run_mode=SequenceRunMode.REPROCESS,
                    missing_history=missing_history,
                    should_cancel=should_cancel,
                    input_frames=input_frames,
                )

            controller = RenderAndProcessModalController(
                self,
                runtime,
                settings,
                adapter=BlenderRenderAdapter(runtime),
                create_processing=create_processing,
                on_cleanup=_clear_active_modal_controller,
            )
            self._controller = controller
            _driver_namespace()[_ACTIVE_CONTROLLER_KEY] = controller
            settings.status = "Initializing Render and Process..."
            try:
                matte_provider = _matte_provider_for_settings(settings)
                feedback_settings = feedback_settings_for_scene(scene)
                image_io = BlenderImageIO(scene)
                reset_frames = parse_reset_frames(reset_frames_text)
                resolution_change = ResolutionChangePolicy(resolution_change_value)
                missing_history = MissingHistoryPolicy(missing_history_value)
                render_session = RawRenderSession.create(
                    scene,
                    view_layer,
                    paths,
                    frame_start=frame_start,
                    frame_end=frame_end,
                    overwrite=overwrite_raw,
                )
                controller.start(context, render_session)
            except Exception as error:
                controller.fail_initialization(frame_start, error)
                return {"CANCELLED"}
            return {"RUNNING_MODAL"}

        progress = _WindowManagerProgress(context.window_manager)
        phase = RenderAndProcessPhase.RENDERING

        def update_phase(value: RenderAndProcessPhase) -> None:
            nonlocal phase
            phase = value
            settings.status = (
                "Rendering raw passes..."
                if value is RenderAndProcessPhase.RENDERING
                else "Processing rendered passes..."
            )

        def render_phase():
            return render_raw_passes(
                scene,
                view_layer,
                paths,
                frame_start=frame_start,
                frame_end=frame_end,
                overwrite=overwrite_raw,
                progress=progress,
            )

        def process_phase(input_frames):
            return process_sequence(
                paths,
                frame_start=frame_start,
                frame_end=frame_end,
                matte_provider=matte_provider,
                settings=feedback_settings,
                image_io=image_io,
                overwrite=overwrite_processed,
                reset_frames=reset_frames,
                resolution_change=resolution_change,
                run_mode=SequenceRunMode.REPROCESS,
                missing_history=missing_history,
                progress=progress,
                input_frames=input_frames,
            )

        try:
            matte_provider = _matte_provider_for_settings(settings)
            feedback_settings = feedback_settings_for_scene(scene)
            image_io = BlenderImageIO(scene)
            reset_frames = parse_reset_frames(reset_frames_text)
            resolution_change = ResolutionChangePolicy(resolution_change_value)
            missing_history = MissingHistoryPolicy(missing_history_value)
            result = render_and_process(render_phase, process_phase, on_phase=update_phase)
        except (RawRenderCancelled, SequenceProcessingCancelled) as error:
            message = f"Render and Process cancelled during {phase.value.lower()}: {error}"
            settings.status = message
            self.report({"WARNING"}, message)
            return {"CANCELLED"}
        except (
            FileExistsError,
            NotImplementedError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            message = f"Render and Process failed during {phase.value.lower()}: {error}"
            settings.status = message
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        frame_count = len(result.processed.frames)
        message = f"Render and Process complete: {frame_count} frame(s)"
        settings.status = message
        self.report({"INFO"}, message)
        return {"FINISHED"}

    def modal(self, context: Context, event: Any) -> set[Any]:
        controller = self._controller
        if controller is None:
            self.report({"ERROR"}, "Render and Process failed: the modal controller is unavailable")
            return {"CANCELLED"}
        return controller.handle_event(event)

    def cancel(self, context: Context) -> None:
        if self._controller is not None:
            self._controller.cancel()


class ODM_OT_process_sequence(Operator):
    """Advance existing-pass processing one frame per Blender timer event."""

    bl_idname = "object_datamosh.process_sequence"
    bl_label = "Process Existing Passes"
    bl_description = "Process existing beauty, vector, and matte EXR sequences"

    _controller: ExistingPassModalController | None

    @classmethod
    def poll(cls, context: Context) -> bool:
        if context.scene is None:
            return False
        settings = settings_for_scene(context.scene)
        return _operation_is_idle(context) and settings.frame_start <= settings.frame_end

    def invoke(self, context: Context, event: Any) -> set[Any]:
        """Enter the same bounded setup path from Blender's interactive invoke dispatch."""
        del event
        return self.execute(context)

    def execute(self, context: Context) -> set[Any]:
        scene = context.scene
        if scene is None:
            self.report({"ERROR"}, "An active scene is required")
            return {"CANCELLED"}
        settings = settings_for_scene(scene)
        runtime = runtime_for_scene(scene)
        controller = ExistingPassModalController(
            self,
            runtime,
            settings,
            on_cleanup=_clear_active_modal_controller,
        )
        self._controller = controller
        _driver_namespace()[_ACTIVE_CONTROLLER_KEY] = controller
        settings.status = "Processing existing passes..."
        try:
            session = ProcessingSession.create(
                sequence_paths_for_scene(scene),
                frame_start=settings.frame_start,
                frame_end=settings.frame_end,
                matte_provider=_matte_provider_for_settings(settings),
                settings=feedback_settings_for_scene(scene),
                image_io=BlenderImageIO(scene),
                overwrite=settings.overwrite_processed,
                reset_frames=parse_reset_frames(settings.reset_frames),
                resolution_change=ResolutionChangePolicy(settings.resolution_change),
                run_mode=SequenceRunMode(settings.sequence_run_mode),
                missing_history=MissingHistoryPolicy(settings.missing_history),
                should_cancel=lambda: controller.cancel_requested,
            )
            controller.start(context, session)
        except Exception as error:
            controller.fail_initialization(settings.frame_start, error)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def modal(self, context: Context, event: Any) -> set[Any]:
        controller = self._controller
        if controller is None:
            self.report({"ERROR"}, "Processing failed: the modal controller is unavailable")
            return {"CANCELLED"}
        return controller.handle_event(event)

    def cancel(self, context: Context) -> None:
        """Release owned modal resources if Blender cancels the operator externally."""
        if self._controller is not None:
            self._controller.cancel()


class ODM_OT_create_vector_calibration(Operator):
    """Create a separate deterministic scene for manual vector calibration."""

    bl_idname = "object_datamosh.create_vector_calibration"
    bl_label = "Create Vector Calibration Scene"
    bl_description = "Create a separate animated ODM_ scene for interpreting vector passes"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return _operation_is_idle(context)

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
        return (
            context.scene is not None
            and _operation_is_idle(context)
            and has_object_index_setup(context.scene)
        )

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
    runtime = _active_operation_runtime() or runtime_for_scene(scene)
    operation_active = runtime.active or _active_modal_controller() is not None
    paths = sequence_paths_for_scene(scene)

    phase_label = {
        OperationPhase.RENDERING.value: "Rendering Raw Passes",
        OperationPhase.PROCESSING.value: "Processing Passes",
    }.get(runtime.phase, runtime.phase.title())
    draw_sidebar(
        layout,
        context,
        settings,
        runtime,
        paths,
        operation_active=operation_active,
        phase_label=phase_label,
    )


_CLASSES = (
    ODM_RuntimeState,
    ODM_Settings,
    ODM_OT_cancel_operation,
    ODM_OT_use_active_object,
    ODM_OT_setup_object_index,
    ODM_OT_render_raw_passes,
    ODM_OT_render_and_process,
    ODM_OT_process_sequence,
    ODM_OT_create_vector_calibration,
    ODM_OT_restore_object_index,
    ODM_PT_sidebar,
)


def _owns_scene_property(attribute: str, property_type: type[PropertyGroup]) -> bool:
    scene_type = cast(Any, Scene)
    deferred_property = getattr(scene_type, attribute, None)
    keywords = getattr(deferred_property, "keywords", {})
    return keywords.get("type") is property_type


def register() -> None:
    """Register classes and the owned scene properties idempotently."""
    scene_type = cast(Any, Scene)
    scene_properties = (
        (_SCENE_SETTINGS_ATTRIBUTE, ODM_Settings),
        (_SCENE_RUNTIME_ATTRIBUTE, ODM_RuntimeState),
    )
    for attribute, property_type in scene_properties:
        if hasattr(scene_type, attribute) and not _owns_scene_property(attribute, property_type):
            raise RuntimeError(
                f"Scene.{attribute} already exists and is not owned by Object Datamosh"
            )

    for cls in _CLASSES:
        if not getattr(cls, "is_registered", False):
            bpy.utils.register_class(cls)
    for attribute, property_type in scene_properties:
        if not hasattr(scene_type, attribute):
            setattr(scene_type, attribute, PointerProperty(type=property_type))


def unregister() -> None:
    """Remove only data registered by this extension when no modal handler owns its classes."""
    if _active_modal_controller() is not None or _active_operation_runtime() is not None:
        raise RuntimeError("Cannot unregister Object Datamosh while an operation is active")
    scene_type = cast(Any, Scene)
    scene_properties = (
        (_SCENE_SETTINGS_ATTRIBUTE, ODM_Settings),
        (_SCENE_RUNTIME_ATTRIBUTE, ODM_RuntimeState),
    )
    for attribute, property_type in scene_properties:
        if _owns_scene_property(attribute, property_type):
            delattr(scene_type, attribute)
    for cls in reversed(_CLASSES):
        if getattr(cls, "is_registered", False):
            bpy.utils.unregister_class(cls)
