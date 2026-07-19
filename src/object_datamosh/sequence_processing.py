"""Sequential processing of resolved beauty, vector, and matte pass files."""

import hashlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, fields
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

import numpy as np

from .core.contracts import FeedbackMode, FeedbackSettings, FeedbackState
from .core.feedback import process_frame
from .core.image_io import ImageSequenceIO
from .core.mattes import MatteProvider
from .core.paths import FramePaths, SequencePaths


def parse_reset_frames(expression: str) -> frozenset[int]:
    """Parse a comma-separated reset-frame expression into unique frame numbers."""
    return frozenset(int(part.strip()) for part in expression.split(",") if part.strip())


class SequenceRunMode(StrEnum):
    """Whether to replace a range from its start or resume a recorded run."""

    REPROCESS = "REPROCESS"
    RESUME = "RESUME"


class MissingHistoryPolicy(StrEnum):
    """How resume handles missing or unreadable recorded history."""

    RESET = "RESET"
    ERROR = "ERROR"


class ResolutionChangePolicy(StrEnum):
    """How sequence processing handles dimensions that change between frames."""

    RESET = "RESET"
    ERROR = "ERROR"


class ProcessingProgress(Protocol):
    """Progress boundary used by sequence processing callers."""

    def begin(self, total: int) -> None: ...

    def update(self, completed: int) -> None: ...

    def end(self) -> None: ...


_MANIFEST_VERSION = 1
_MANIFEST_FILENAME = "ODM_sequence_manifest.json"


def sequence_manifest_path(paths: SequencePaths) -> Path:
    """Return the recovery-manifest path for a processed sequence."""
    return paths.root / "processed" / _MANIFEST_FILENAME


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
    reset_frames: frozenset[int] = frozenset(),
    resolution_change: ResolutionChangePolicy = ResolutionChangePolicy.ERROR,
    run_mode: SequenceRunMode = SequenceRunMode.REPROCESS,
    missing_history: MissingHistoryPolicy = MissingHistoryPolicy.ERROR,
    progress: ProcessingProgress | None = None,
    should_cancel: Callable[[], bool] | None = None,
    input_frames: tuple[FramePaths, ...] | None = None,
) -> SequenceProcessingResult:
    """Process a resolved sequence strictly from ``frame_start`` through ``frame_end``.

    When ``input_frames`` is supplied, beauty, vector, and Object Index matte inputs are read from
    those discovered paths rather than reconstructed from the sequence naming convention.
    """
    if frame_start > frame_end:
        raise ValueError("frame_start must not be greater than frame_end")
    frame_numbers = tuple(range(frame_start, frame_end + 1))
    if input_frames is not None and tuple(frame.frame for frame in input_frames) != frame_numbers:
        raise ValueError("input_frames must contain the complete configured frame range in order")
    resolved_inputs = (
        {frame.frame: frame for frame in input_frames}
        if input_frames is not None
        else {frame: paths.frame(frame) for frame in frame_numbers}
    )
    manifest_path = sequence_manifest_path(paths)
    fingerprint = _settings_fingerprint(settings, matte_provider)
    state: FeedbackState | None = None
    first_frame = frame_start
    recovery_reset_frame: int | None = None
    completed: list[int] = []

    if run_mode is SequenceRunMode.RESUME:
        manifest = _read_manifest(manifest_path)
        _validate_manifest(
            manifest,
            frame_start=frame_start,
            frame_end=frame_end,
            fingerprint=fingerprint,
            reset_frames=reset_frames,
            resolution_change=resolution_change,
        )
        completed = _completed_frames(manifest)
        recorded_completion_count = len(completed)
        missing_index = next(
            (
                index
                for index, frame_number in enumerate(completed)
                if not paths.frame(frame_number).processed.exists()
            ),
            None,
        )
        if missing_index is not None:
            missing_frame = completed[missing_index]
            if missing_history is MissingHistoryPolicy.ERROR:
                raise RuntimeError(f"Resume history is missing for frame {missing_frame}")
            completed = completed[:missing_index]
            first_frame = missing_frame
            recovery_reset_frame = missing_frame
        if completed:
            previous_number = completed[-1]
            previous_frame = paths.frame(previous_number)
            try:
                if settings.mode is FeedbackMode.TRAIL:
                    state = _restore_trail_state(
                        completed,
                        paths=paths,
                        matte_provider=matte_provider,
                        settings=settings,
                        image_io=image_io,
                        reset_frames=reset_frames,
                    )
                else:
                    previous_history = image_io.read_rgba(previous_frame.processed)
                    previous_matte = image_io.read_mask(
                        matte_provider.path_for_frame(previous_number, paths)
                    )
                    state = FeedbackState(previous_history, previous_matte, previous_number)
                if not np.all(np.isfinite(state.history)):
                    raise ValueError("history must contain only finite values")
                if not np.all(np.isfinite(state.history_matte)) or np.any(
                    (state.history_matte < 0.0) | (state.history_matte > 1.0)
                ):
                    raise ValueError("history_matte coverage must be finite and between 0 and 1")
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                if missing_history is MissingHistoryPolicy.ERROR:
                    raise RuntimeError(
                        f"Resume history is invalid for frame {previous_number}: {error}"
                    ) from error
                completed.pop()
                first_frame = previous_number
                recovery_reset_frame = previous_number
            else:
                first_frame = max(first_frame, previous_number + 1)
        if len(completed) != recorded_completion_count:
            _write_manifest(
                manifest_path,
                _new_manifest(
                    frame_start,
                    frame_end,
                    fingerprint,
                    reset_frames,
                    resolution_change,
                    completed,
                ),
            )
    else:
        if not overwrite:
            collisions = tuple(
                paths.frame(frame_number).processed
                for frame_number in frame_numbers
                if paths.frame(frame_number).processed.exists()
            )
            if collisions:
                preview = ", ".join(str(path) for path in collisions[:3])
                raise FileExistsError(
                    f"Processed output exists and overwrite is disabled: {preview}"
                )
        _write_manifest(
            manifest_path,
            _new_manifest(
                frame_start,
                frame_end,
                fingerprint,
                reset_frames,
                resolution_change,
                completed,
            ),
        )

    outputs: list[Path] = []
    progress_started = False
    try:
        if progress is not None:
            progress.begin(max(0, frame_end - first_frame + 1))
            progress_started = True
        for frame_number in range(first_frame, frame_end + 1):
            if should_cancel is not None and should_cancel():
                raise SequenceProcessingCancelled(tuple(outputs))
            frame = paths.frame(frame_number)
            raw_frame = resolved_inputs[frame_number]
            matte_path = matte_provider.path_for_frame(frame_number, paths)
            if matte_path == frame.matte:
                matte_path = raw_frame.matte
            try:
                beauty = image_io.read_rgba(raw_frame.beauty)
            except FileNotFoundError:
                raise FileNotFoundError(
                    f"Missing beauty input for frame {frame_number}: {raw_frame.beauty}"
                ) from None
            try:
                motion = image_io.read_rgba(raw_frame.vector)
            except FileNotFoundError:
                raise FileNotFoundError(
                    f"Missing vector input for frame {frame_number}: {raw_frame.vector}"
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
                raw_frame.beauty,
                raw_frame.vector,
                matte_path,
                settings.motion_channels.value,
            )
            resolution_changed = state is not None and state.history.shape != beauty.shape
            if resolution_changed and resolution_change is ResolutionChangePolicy.ERROR:
                raise ValueError(
                    f"Resolution changed at frame {frame_number}: "
                    f"{state.history.shape[:2]} -> {beauty.shape[:2]}"
                )
            output, state = process_frame(
                beauty,
                motion,
                matte,
                None if resolution_changed else state,
                frame_number,
                settings,
                force_reset=(
                    frame_number in (frame_start, recovery_reset_frame)
                    or frame_number in reset_frames
                    or resolution_changed
                ),
            )
            image_io.write_rgba(frame.processed, output)
            logging.getLogger(__name__).info(
                "Wrote processed frame %d: %s", frame_number, frame.processed
            )
            outputs.append(frame.processed)
            completed.append(frame_number)
            _write_manifest(
                manifest_path,
                _new_manifest(
                    frame_start,
                    frame_end,
                    fingerprint,
                    reset_frames,
                    resolution_change,
                    completed,
                ),
            )
            if progress is not None:
                progress.update(len(outputs))
    finally:
        if progress is not None and progress_started:
            progress.end()
    return SequenceProcessingResult(tuple(outputs))


def _restore_trail_state(
    completed: list[int],
    *,
    paths: SequencePaths,
    matte_provider: MatteProvider,
    settings: FeedbackSettings,
    image_io: ImageSequenceIO,
    reset_frames: frozenset[int],
) -> FeedbackState:
    """Rebuild trail coverage while trusting recorded processed color as history."""
    state: FeedbackState | None = None
    for frame_number in completed:
        frame = paths.frame(frame_number)
        history = image_io.read_rgba(frame.processed)
        matte = image_io.read_mask(matte_provider.path_for_frame(frame_number, paths))
        reset = (
            state is None or frame_number in reset_frames or state.history.shape != history.shape
        )
        if reset:
            state = FeedbackState(history, matte, frame_number)
            continue
        motion = image_io.read_rgba(frame.vector)
        _output, next_state = process_frame(
            history,
            motion,
            matte,
            state,
            frame_number,
            settings,
        )
        state = FeedbackState(history, next_state.history_matte, frame_number)
    if state is None:
        raise ValueError("trail history requires at least one completed frame")
    return state


def _settings_fingerprint(settings: FeedbackSettings, matte_provider: MatteProvider) -> str:
    payload = {
        field.name: (
            getattr(settings, field.name).value
            if isinstance(getattr(settings, field.name), StrEnum)
            else getattr(settings, field.name)
        )
        for field in fields(settings)
    }
    provider_settings: dict[str, object] = {}
    for name in ("directory", "prefix", "extension", "padding"):
        if hasattr(matte_provider, name):
            value = getattr(matte_provider, name)
            provider_settings[name] = str(value) if isinstance(value, Path) else value
    payload["matte_provider"] = {
        "type": type(matte_provider).__name__,
        "settings": provider_settings,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _new_manifest(
    frame_start: int,
    frame_end: int,
    fingerprint: str,
    reset_frames: frozenset[int],
    resolution_change: ResolutionChangePolicy,
    completed: list[int],
) -> dict[str, object]:
    return {
        "schema_version": _MANIFEST_VERSION,
        "frame_start": frame_start,
        "frame_end": frame_end,
        "settings_fingerprint": fingerprint,
        "reset_frames": sorted(reset_frames),
        "resolution_change": resolution_change.value,
        "completed_frames": completed,
    }


def _write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_manifest(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"No sequence recovery manifest exists: {path}") from None
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Sequence recovery manifest is invalid: {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"Sequence recovery manifest is invalid: {path}")
    return value


def _validate_manifest(
    manifest: dict[str, object],
    *,
    frame_start: int,
    frame_end: int,
    fingerprint: str,
    reset_frames: frozenset[int],
    resolution_change: ResolutionChangePolicy,
) -> None:
    expected = {
        "schema_version": _MANIFEST_VERSION,
        "frame_start": frame_start,
        "frame_end": frame_end,
        "settings_fingerprint": fingerprint,
        "reset_frames": sorted(reset_frames),
        "resolution_change": resolution_change.value,
    }
    for name, value in expected.items():
        if manifest.get(name) != value:
            raise ValueError(f"Sequence recovery manifest is incompatible: {name} changed")
    completed = _completed_frames(manifest)
    if completed != list(range(frame_start, frame_start + len(completed))):
        raise ValueError("Sequence recovery manifest has discontinuous completed frames")
    if completed and completed[-1] > frame_end:
        raise ValueError("Sequence recovery manifest has completed frames outside the range")


def _completed_frames(manifest: dict[str, object]) -> list[int]:
    value = manifest.get("completed_frames")
    if not isinstance(value, list) or any(
        not isinstance(frame, int) or isinstance(frame, bool) for frame in value
    ):
        raise ValueError("Sequence recovery manifest has invalid completed frames")
    return cast(list[int], value)
