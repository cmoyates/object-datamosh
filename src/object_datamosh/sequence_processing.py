"""Sequential processing of resolved beauty, vector, and matte pass files."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .core.contracts import FeedbackSettings
from .core.feedback import process_frame
from .core.image_io import ImageSequenceIO
from .core.mattes import MatteProvider
from .core.paths import SequencePaths


class ProcessingProgress(Protocol):
    """Progress boundary used by sequence processing callers."""

    def begin(self, total: int) -> None: ...

    def update(self, completed: int) -> None: ...

    def end(self) -> None: ...


class SequenceProcessingCancelled(RuntimeError):
    """Raised at a frame boundary after a caller requests cancellation."""

    def __init__(self, completed_frames: tuple[Path, ...]) -> None:
        super().__init__(f"Sequence processing cancelled after {len(completed_frames)} frame(s)")
        self.completed_frames = completed_frames


@dataclass(frozen=True, slots=True)
class SequenceProcessingResult:
    """Processed output files produced for a completed frame range."""

    frames: tuple[Path, ...]


def process_sequence(
    paths: SequencePaths,
    *,
    frame_start: int,
    frame_end: int,
    matte_provider: MatteProvider,
    settings: FeedbackSettings,
    image_io: ImageSequenceIO,
    overwrite: bool = False,
    progress: ProcessingProgress | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> SequenceProcessingResult:
    """Process a resolved sequence strictly from ``frame_start`` through ``frame_end``."""
    if frame_start > frame_end:
        raise ValueError("frame_start must not be greater than frame_end")
    if not overwrite:
        collisions = tuple(
            paths.frame(frame_number).processed
            for frame_number in range(frame_start, frame_end + 1)
            if paths.frame(frame_number).processed.exists()
        )
        if collisions:
            preview = ", ".join(str(path) for path in collisions[:3])
            raise FileExistsError(f"Processed output exists and overwrite is disabled: {preview}")

    state = None
    outputs: list[Path] = []
    progress_started = False
    try:
        if progress is not None:
            progress.begin(frame_end - frame_start + 1)
            progress_started = True
        for frame_number in range(frame_start, frame_end + 1):
            if should_cancel is not None and should_cancel():
                raise SequenceProcessingCancelled(tuple(outputs))
            frame = paths.frame(frame_number)
            matte_path = matte_provider.path_for_frame(frame_number, paths)
            try:
                beauty = image_io.read_rgba(frame.beauty)
            except FileNotFoundError:
                raise FileNotFoundError(
                    f"Missing beauty input for frame {frame_number}: {frame.beauty}"
                ) from None
            try:
                motion = image_io.read_rgba(frame.vector)
            except FileNotFoundError:
                raise FileNotFoundError(
                    f"Missing vector input for frame {frame_number}: {frame.vector}"
                ) from None
            try:
                matte = image_io.read_mask(matte_path)
            except FileNotFoundError:
                raise FileNotFoundError(
                    f"Missing matte input for frame {frame_number}: {matte_path}"
                ) from None
            logging.getLogger(__name__).info(
                "Processing frame %d: beauty=%s, vector=%s, matte=%s, motion_channels=%s",
                frame_number,
                frame.beauty,
                frame.vector,
                matte_path,
                settings.motion_channels.value,
            )
            output, state = process_frame(
                beauty,
                motion,
                matte,
                state,
                frame_number,
                settings,
            )
            image_io.write_rgba(frame.processed, output)
            logging.getLogger(__name__).info(
                "Wrote processed frame %d: %s", frame_number, frame.processed
            )
            outputs.append(frame.processed)
            if progress is not None:
                progress.update(len(outputs))
    finally:
        if progress is not None and progress_started:
            progress.end()
    return SequenceProcessingResult(tuple(outputs))
