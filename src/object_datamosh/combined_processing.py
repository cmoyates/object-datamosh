"""Immutable processing configuration shared by combined workflow drivers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .core.contracts import FeedbackSettings
from .core.image_io import ImageSequenceIO
from .core.mattes import MatteProvider
from .core.paths import FramePaths, SequencePaths
from .sequence_processing import (
    ProcessingProgress,
    ProcessingSession,
    ResolutionChangePolicy,
    SequenceProcessingFrameError,
    SequenceProcessingResult,
    SequenceRunMode,
    process_sequence,
)


@dataclass(frozen=True, slots=True)
class CombinedProcessingConfiguration:
    """Validated invocation snapshot for reprocessing newly rendered frames."""

    paths: SequencePaths
    frame_start: int
    frame_end: int
    matte_provider: MatteProvider
    feedback_settings: FeedbackSettings
    image_io: ImageSequenceIO
    overwrite: bool
    reset_frames: frozenset[int]
    resolution_change: ResolutionChangePolicy
    extension_version: str | None = None
    blender_version: str | None = None

    def create_session(
        self,
        input_frames: tuple[FramePaths, ...],
        should_cancel: Callable[[], bool],
    ) -> ProcessingSession:
        """Create the incremental processing phase for an interactive combined run."""
        return ProcessingSession.create(
            self.paths,
            frame_start=self.frame_start,
            frame_end=self.frame_end,
            matte_provider=self.matte_provider,
            settings=self.feedback_settings,
            image_io=self.image_io,
            overwrite=self.overwrite,
            reset_frames=self.reset_frames,
            resolution_change=self.resolution_change,
            run_mode=SequenceRunMode.REPROCESS,
            should_cancel=should_cancel,
            input_frames=input_frames,
            extension_version=self.extension_version,
            blender_version=self.blender_version,
        )

    def process(
        self,
        input_frames: tuple[FramePaths, ...],
        progress: ProcessingProgress,
    ) -> SequenceProcessingResult:
        """Delegate synchronous execution to the canonical sequence driver."""
        return process_sequence(
            self.paths,
            frame_start=self.frame_start,
            frame_end=self.frame_end,
            matte_provider=self.matte_provider,
            settings=self.feedback_settings,
            image_io=self.image_io,
            overwrite=self.overwrite,
            reset_frames=self.reset_frames,
            resolution_change=self.resolution_change,
            run_mode=SequenceRunMode.REPROCESS,
            progress=progress,
            input_frames=input_frames,
            frame_error_factory=SequenceProcessingFrameError,
            extension_version=self.extension_version,
            blender_version=self.blender_version,
        )
