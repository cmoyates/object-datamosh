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
    SequenceProcessingCancelled,
    SequenceProcessingResult,
    SequenceRunMode,
)


class CombinedProcessingFailure(RuntimeError):
    """Processing failure with the exact affected frame preserved for its driver."""

    def __init__(self, frame: int, error: Exception) -> None:
        super().__init__(str(error))
        self.frame = frame


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
        )

    def process(
        self,
        input_frames: tuple[FramePaths, ...],
        progress: ProcessingProgress,
    ) -> SequenceProcessingResult:
        """Run the same snapshotted phase synchronously and retain failure frame context."""
        try:
            session = self.create_session(input_frames, lambda: False)
        except Exception as error:
            raise CombinedProcessingFailure(self.frame_start, error) from error
        try:
            progress.begin(self.frame_end - self.frame_start + 1)
        except Exception as error:
            raise CombinedProcessingFailure(self.frame_start, error) from error
        try:
            while not session.is_finished:
                frame_number = session.current_frame
                completed_before = len(session.completed_frames)
                try:
                    session.process_next_frame()
                except SequenceProcessingCancelled:
                    raise
                except Exception as error:
                    raise CombinedProcessingFailure(frame_number, error) from error
                if len(session.completed_frames) > completed_before:
                    progress.update(len(session.completed_frames))
            try:
                return session.result
            except Exception as error:
                raise CombinedProcessingFailure(session.current_frame, error) from error
        finally:
            try:
                progress.end()
            except Exception as error:
                raise CombinedProcessingFailure(session.current_frame, error) from error
