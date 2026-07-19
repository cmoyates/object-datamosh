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


class SequenceProcessingFrameError(RuntimeError):
    """Synchronous processing failure attributed to its active frame."""

    def __init__(self, frame: int, error: Exception) -> None:
        super().__init__(str(error))
        self.frame = frame


@dataclass(frozen=True, slots=True)
class SequenceProcessingResult:
    """Processed output files produced for a completed frame range."""

    frames: tuple[Path, ...]


@dataclass(slots=True)
class ProcessingSession:
    """A sequence run that advances no more than one complete frame at a time."""

    paths: SequencePaths
    frame_start: int
    frame_end: int
    matte_provider: MatteProvider
    settings: FeedbackSettings
    image_io: ImageSequenceIO
    overwrite: bool
    reset_frames: frozenset[int]
    resolution_change: ResolutionChangePolicy
    run_mode: SequenceRunMode
    missing_history: MissingHistoryPolicy
    should_cancel: Callable[[], bool] | None
    resolved_inputs: dict[int, FramePaths]
    settings_fingerprint: str
    manifest_path: Path
    current_frame: int
    completed_frames: tuple[Path, ...]
    _completed_numbers: list[int]
    _state: FeedbackState | None = None
    _recovery_reset_frame: int | None = None
    _trail_recovery_frames: tuple[int, ...] = ()
    _trail_recovery_index: int = 0
    _is_finished: bool = False
    _terminal_error: Exception | None = None

    @classmethod
    def create(
        cls,
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
        should_cancel: Callable[[], bool] | None = None,
        input_frames: tuple[FramePaths, ...] | None = None,
    ) -> "ProcessingSession":
        """Initialize one sequence run without processing a frame."""
        if frame_start > frame_end:
            raise ValueError("frame_start must not be greater than frame_end")
        frame_numbers = tuple(range(frame_start, frame_end + 1))
        if (
            input_frames is not None
            and tuple(frame.frame for frame in input_frames) != frame_numbers
        ):
            raise ValueError(
                "input_frames must contain the complete configured frame range in order"
            )
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
        trail_recovery_frames: tuple[int, ...] = ()
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
                if settings.mode is FeedbackMode.TRAIL:
                    # Rebuilding and validating trail coverage can be as expensive as processing
                    # the completed prefix. Defer every frame so an incremental caller remains
                    # bounded, including a fully completed resume whose history still needs
                    # validation under the configured missing-history policy.
                    trail_recovery_frames = tuple(completed)
                    first_frame = max(first_frame, previous_number + 1)
                else:
                    try:
                        previous_history = image_io.read_rgba(previous_frame.processed)
                        previous_matte = image_io.read_mask(
                            matte_provider.path_for_frame(previous_number, paths)
                        )
                        state = FeedbackState(previous_history, previous_matte, previous_number)
                        _validate_history_state(state)
                    except (OSError, RuntimeError, TypeError, ValueError) as error:
                        if missing_history is MissingHistoryPolicy.ERROR:
                            raise RuntimeError(
                                f"Resume history is invalid for frame {previous_number}: {error}"
                            ) from error
                        completed.pop()
                        first_frame = previous_number
                        recovery_reset_frame = previous_number
                        state = None
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
        return cls(
            paths=paths,
            frame_start=frame_start,
            frame_end=frame_end,
            matte_provider=matte_provider,
            settings=settings,
            image_io=image_io,
            overwrite=overwrite,
            reset_frames=reset_frames,
            resolution_change=resolution_change,
            run_mode=run_mode,
            missing_history=missing_history,
            should_cancel=should_cancel,
            resolved_inputs=resolved_inputs,
            settings_fingerprint=fingerprint,
            manifest_path=manifest_path,
            current_frame=first_frame,
            completed_frames=(),
            _completed_numbers=completed,
            _state=state,
            _recovery_reset_frame=recovery_reset_frame,
            _trail_recovery_frames=trail_recovery_frames,
            _is_finished=first_frame > frame_end and not trail_recovery_frames,
        )

    @property
    def is_finished(self) -> bool:
        """Whether the session reached a terminal state."""
        return self._is_finished

    @property
    def retained_frames(self) -> tuple[int, ...]:
        """Manifest frames currently trusted as the contiguous completed prefix."""
        return tuple(self._completed_numbers)

    @property
    def recovery_frame(self) -> int | None:
        """Completed frame whose trail state will be restored by the next step."""
        if self._trail_recovery_index >= len(self._trail_recovery_frames):
            return None
        return self._trail_recovery_frames[self._trail_recovery_index]

    @property
    def result(self) -> SequenceProcessingResult:
        """Return outputs after successful completion."""
        if not self._is_finished:
            raise RuntimeError("Sequence processing is not finished")
        if self._terminal_error is not None:
            raise RuntimeError(
                "Sequence processing did not complete successfully"
            ) from self._terminal_error
        return SequenceProcessingResult(self.completed_frames)

    def process_next_frame(self) -> None:
        """Advance at most one recovery or output frame, then yield to the caller."""
        if self._is_finished:
            return
        try:
            if self.should_cancel is not None and self.should_cancel():
                self._is_finished = True
                raise SequenceProcessingCancelled(self.completed_frames)
            if self.recovery_frame is not None:
                self._restore_next_trail_frame()
            else:
                self._process_current_frame()
        except Exception as error:
            self._terminal_error = error
            self._is_finished = True
            raise

    def _restore_next_trail_frame(self) -> None:
        frame_number = self.recovery_frame
        if frame_number is None:
            return
        try:
            self._state = _restore_trail_frame(
                frame_number,
                state=self._state,
                paths=self.paths,
                matte_provider=self.matte_provider,
                settings=self.settings,
                image_io=self.image_io,
                reset_frames=self.reset_frames,
            )
            _validate_history_state(self._state)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            if self.missing_history is MissingHistoryPolicy.ERROR:
                raise RuntimeError(
                    f"Resume history is invalid for frame {frame_number}: {error}"
                ) from error
            failed_index = self._completed_numbers.index(frame_number)
            self._completed_numbers = self._completed_numbers[:failed_index]
            self.current_frame = frame_number
            self._recovery_reset_frame = frame_number
            self._state = None
            self._trail_recovery_frames = ()
            self._trail_recovery_index = 0
            _write_manifest(
                self.manifest_path,
                _new_manifest(
                    self.frame_start,
                    self.frame_end,
                    self.settings_fingerprint,
                    self.reset_frames,
                    self.resolution_change,
                    self._completed_numbers,
                ),
            )
            return
        self._trail_recovery_index += 1
        if self._trail_recovery_index == len(self._trail_recovery_frames):
            self._trail_recovery_frames = ()
            self._trail_recovery_index = 0
            if self.current_frame > self.frame_end:
                self._is_finished = True

    def _process_current_frame(self) -> None:
        frame_number = self.current_frame
        frame = self.paths.frame(frame_number)
        raw_frame = self.resolved_inputs[frame_number]
        matte_path = self.matte_provider.path_for_frame(frame_number, self.paths)
        if matte_path == frame.matte:
            matte_path = raw_frame.matte
        try:
            beauty = self.image_io.read_rgba(raw_frame.beauty)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Missing beauty input for frame {frame_number}: {raw_frame.beauty}"
            ) from None
        try:
            motion = self.image_io.read_rgba(raw_frame.vector)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Missing vector input for frame {frame_number}: {raw_frame.vector}"
            ) from None
        try:
            matte = self.image_io.read_mask(matte_path)
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
            self.settings.motion_channels.value,
        )
        resolution_changed = self._state is not None and self._state.history.shape != beauty.shape
        if resolution_changed and self.resolution_change is ResolutionChangePolicy.ERROR:
            raise ValueError(
                f"Resolution changed at frame {frame_number}: "
                f"{self._state.history.shape[:2]} -> {beauty.shape[:2]}"
            )
        output, self._state = process_frame(
            beauty,
            motion,
            matte,
            None if resolution_changed else self._state,
            frame_number,
            self.settings,
            force_reset=(
                frame_number in (self.frame_start, self._recovery_reset_frame)
                or frame_number in self.reset_frames
                or resolution_changed
            ),
        )
        self.image_io.write_rgba(frame.processed, output)
        logging.getLogger(__name__).info(
            "Wrote processed frame %d: %s", frame_number, frame.processed
        )
        committed_numbers = [*self._completed_numbers, frame_number]
        _write_manifest(
            self.manifest_path,
            _new_manifest(
                self.frame_start,
                self.frame_end,
                self.settings_fingerprint,
                self.reset_frames,
                self.resolution_change,
                committed_numbers,
            ),
        )
        # Publish completion only after the recovery manifest atomically commits the frame.
        self._completed_numbers = committed_numbers
        self.completed_frames = (*self.completed_frames, frame.processed)
        if frame_number == self.frame_end:
            self._is_finished = True
        else:
            self.current_frame += 1


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
    frame_error_factory: Callable[[int, Exception], Exception] | None = None,
) -> SequenceProcessingResult:
    """Process a sequence synchronously, optionally attributing failures to their frame."""
    try:
        session = ProcessingSession.create(
            paths,
            frame_start=frame_start,
            frame_end=frame_end,
            matte_provider=matte_provider,
            settings=settings,
            image_io=image_io,
            overwrite=overwrite,
            reset_frames=reset_frames,
            resolution_change=resolution_change,
            run_mode=run_mode,
            missing_history=missing_history,
            should_cancel=should_cancel,
            input_frames=input_frames,
        )
    except Exception as error:
        if frame_error_factory is None:
            raise
        raise frame_error_factory(frame_start, error) from error

    progress_started = False
    try:
        # Synchronous callers historically restored Resume history before opening output progress.
        # Keep that contract while modal callers continue to advance recovery one timer step.
        while session.recovery_frame is not None:
            recovery_frame = session.recovery_frame
            try:
                session.process_next_frame()
            except SequenceProcessingCancelled:
                raise
            except Exception as error:
                if frame_error_factory is None:
                    raise
                raise frame_error_factory(recovery_frame, error) from error
        if progress is not None:
            remaining = max(0, frame_end - session.current_frame + 1)
            try:
                progress.begin(remaining)
            except Exception as error:
                if frame_error_factory is None:
                    raise
                raise frame_error_factory(session.current_frame, error) from error
            progress_started = True
        while not session.is_finished:
            frame_number = session.current_frame
            completed_before = len(session.completed_frames)
            try:
                session.process_next_frame()
            except SequenceProcessingCancelled:
                raise
            except Exception as error:
                if frame_error_factory is None:
                    raise
                raise frame_error_factory(frame_number, error) from error
            if progress is not None and len(session.completed_frames) > completed_before:
                try:
                    progress.update(len(session.completed_frames))
                except Exception as error:
                    if frame_error_factory is None:
                        raise
                    raise frame_error_factory(frame_number, error) from error
        try:
            result = session.result
        except Exception as error:
            if frame_error_factory is None:
                raise
            raise frame_error_factory(session.current_frame, error) from error
    except Exception as primary_error:
        if progress is not None and progress_started:
            try:
                progress.end()
            except Exception as cleanup_error:
                primary_error.add_note(f"Progress cleanup also failed: {cleanup_error}")
        raise
    if progress is not None and progress_started:
        try:
            progress.end()
        except Exception as error:
            if frame_error_factory is None:
                raise
            raise frame_error_factory(session.current_frame, error) from error
    return result


def _restore_trail_frame(
    frame_number: int,
    *,
    state: FeedbackState | None,
    paths: SequencePaths,
    matte_provider: MatteProvider,
    settings: FeedbackSettings,
    image_io: ImageSequenceIO,
    reset_frames: frozenset[int],
) -> FeedbackState:
    """Rebuild one frame of trail coverage while trusting processed color as history."""
    frame = paths.frame(frame_number)
    history = image_io.read_rgba(frame.processed)
    matte = image_io.read_mask(matte_provider.path_for_frame(frame_number, paths))
    reset = state is None or frame_number in reset_frames or state.history.shape != history.shape
    if reset:
        return FeedbackState(history, matte, frame_number)
    motion = image_io.read_rgba(frame.vector)
    _output, next_state = process_frame(
        history,
        motion,
        matte,
        state,
        frame_number,
        settings,
    )
    return FeedbackState(history, next_state.history_matte, frame_number)


def _validate_history_state(state: FeedbackState) -> None:
    """Reject resume state that cannot safely seed another frame."""
    if not np.all(np.isfinite(state.history)):
        raise ValueError("history must contain only finite values")
    if not np.all(np.isfinite(state.history_matte)) or np.any(
        (state.history_matte < 0.0) | (state.history_matte > 1.0)
    ):
        raise ValueError("history_matte coverage must be finite and between 0 and 1")


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
