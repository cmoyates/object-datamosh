"""Sequential processing of resolved beauty, vector, and matte pass files."""

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, fields, is_dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, cast

import numpy as np

from .core.contracts import FeedbackMode, FeedbackSettings, FeedbackState, HistorySource
from .core.diagnostics import FrameDiagnostics, ProcessingDiagnostics, assess_near_no_op
from .core.feedback import process_frame, process_frame_with_diagnostics
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


_MANIFEST_VERSION = 5
_IMAGE_ORIENTATION = "display_top_left_v1"
_MANIFEST_FILENAME = "ODM_sequence_manifest.json"
_REPORT_FILENAME = "ODM_processing_report.json"
MAX_REPORTED_TIMING_FRAMES = 96
# Diagnostics are observational; recovery remains atomically committed every frame. Keeping this
# cadence internal avoids changing artistic settings or resume fingerprints.
_REPORT_CHECKPOINT_INTERVAL_FRAMES = 10


@dataclass(slots=True)
class _FrameTiming:
    frame_number: int
    reset: bool | None
    started_ns: int
    stages_ns: dict[str, int]
    outcome: str = "RUNNING"
    total_frame_ns: int | None = None

    def payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "frame_number": self.frame_number,
            "reset": self.reset,
            "outcome": self.outcome,
            "stage_order": list(self.stages_ns),
            "stages_ns": dict(self.stages_ns),
        }
        if self.total_frame_ns is not None:
            payload["total_frame_ns"] = self.total_frame_ns
        return payload


def sequence_manifest_path(paths: SequencePaths) -> Path:
    """Return the recovery-manifest path for a processed sequence."""
    return paths.root / "processed" / _MANIFEST_FILENAME


def processing_report_path(paths: SequencePaths) -> Path:
    """Return the bounded processing-diagnostics report path beside the manifest."""
    return paths.root / "processed" / _REPORT_FILENAME


def processing_configuration_name(settings: FeedbackSettings) -> str:
    """Return the concise identity shared by logs and visible run status."""
    history = "Full Frame" if settings.history_source is HistorySource.FULL_FRAME else "Target Only"
    mode = "Trail" if settings.mode is FeedbackMode.TRAIL else "Hard Localized"
    return f"{history} / {mode}"


def processing_configuration_summary(settings: FeedbackSettings) -> str:
    """Return the preflight summary of the most consequential feedback controls."""
    return (
        f"{processing_configuration_name(settings)} | Persistence {settings.persistence:g} | "
        f"Block {settings.block_size} | Diffusion {settings.diffusion:g} | "
        f"Refresh {settings.refresh_probability:g}"
    )


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
    effective_settings: dict[str, object]
    manifest_path: Path
    report_path: Path
    current_frame: int
    completed_frames: tuple[Path, ...]
    _completed_numbers: list[int]
    _frame_diagnostics: list[FrameDiagnostics]
    _timer_ns: Callable[[], int]
    _frame_timings: list[_FrameTiming]
    _active_timing: _FrameTiming | None = None
    _state: FeedbackState | None = None
    _recovery_reset_frame: int | None = None
    _trail_recovery_frames: tuple[int, ...] = ()
    _trail_recovery_index: int = 0
    _is_finished: bool = False
    _terminal_error: Exception | None = None
    _near_no_op_reported: bool = False

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
        extension_version: str | None = None,
        blender_version: str | None = None,
        timer_ns: Callable[[], int] = time.perf_counter_ns,
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
        effective_settings = _effective_settings_snapshot(
            settings,
            matte_provider,
            reset_frames=reset_frames,
            resolution_change=resolution_change,
            extension_version=extension_version,
            blender_version=blender_version,
        )
        logging.getLogger(__name__).info(
            "Initialized processing configuration: %s; manifest=%s",
            processing_configuration_summary(settings),
            manifest_path,
        )
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
                history_source=settings.history_source,
                reset_frames=reset_frames,
                resolution_change=resolution_change,
                semantic_settings=_semantic_settings_snapshot(settings, matte_provider),
            )
            effective_settings = cast(dict[str, object], manifest["effective_settings"])
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
                        settings.history_source,
                        reset_frames,
                        resolution_change,
                        completed,
                        effective_settings,
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
                    settings.history_source,
                    reset_frames,
                    resolution_change,
                    completed,
                    effective_settings,
                ),
            )
        session = cls(
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
            effective_settings=effective_settings,
            manifest_path=manifest_path,
            report_path=processing_report_path(paths),
            current_frame=first_frame,
            completed_frames=(),
            _completed_numbers=completed,
            _frame_diagnostics=[],
            _timer_ns=timer_ns,
            _frame_timings=[],
            _state=state,
            _recovery_reset_frame=recovery_reset_frame,
            _trail_recovery_frames=trail_recovery_frames,
            _is_finished=first_frame > frame_end and not trail_recovery_frames,
        )
        try:
            session._write_report("SUCCESS" if session._is_finished else "RUNNING")
        except Exception as error:
            raise RuntimeError(
                f"Diagnostics report write failed during session initialization: {error}"
            ) from error
        return session

    @property
    def performance_timings(self) -> dict[str, object]:
        """Return bounded observational timing metadata for reports and tests."""
        detailed = self._frame_timings[-MAX_REPORTED_TIMING_FRAMES:]
        stage_totals: dict[str, int] = {}
        for timing in detailed:
            for name, duration in timing.stages_ns.items():
                stage_totals[name] = stage_totals.get(name, 0) + duration
        largest = sorted(stage_totals.items(), key=lambda item: (-item[1], item[0]))
        payload: dict[str, object] = {
            "schema_version": 1,
            "clock": "perf_counter_ns",
            "unit": "nanoseconds",
            "observational_only": True,
            "history_limit": MAX_REPORTED_TIMING_FRAMES,
            "frames": [timing.payload() for timing in detailed],
            "frames_omitted": max(0, len(self._frame_timings) - len(detailed)),
            "largest_stages": [
                {"stage": name, "duration_ns": duration} for name, duration in largest
            ],
        }
        if self._active_timing is not None:
            payload["active_frame"] = self._active_timing.payload()
        return payload

    @property
    def configuration_name(self) -> str:
        """Concise immutable configuration identity for this session."""
        return processing_configuration_name(self.settings)

    @property
    def advisory_warnings(self) -> tuple[str, ...]:
        """Evidence-based advisory warnings for the completed prefix."""
        assessment = assess_near_no_op(
            ProcessingDiagnostics.from_frames(tuple(self._frame_diagnostics)), self.settings
        )
        messages: list[str] = []
        if assessment.likely_near_no_op:
            messages.append("Likely ineffective feedback: " + "; ".join(assessment.causes))
        elif assessment.causes:
            messages.append("Diagnostics: " + "; ".join(assessment.causes))
        return tuple(messages)

    def write_terminal_report(
        self,
        outcome: Literal["CANCELLED", "FAILURE"],
        *,
        failure: str | None = None,
    ) -> None:
        """Persist a terminal report when a caller stops before the next processing step."""
        self._write_report(outcome, failure=failure)

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
                if self._is_finished:
                    self._write_report("SUCCESS")
            else:
                self._process_current_frame()
        except Exception as error:
            self._terminal_error = error
            self._is_finished = True
            outcome = "CANCELLED" if isinstance(error, SequenceProcessingCancelled) else "FAILURE"
            try:
                if self._active_timing is not None:
                    self._commit_frame_report(
                        outcome, failure=None if outcome == "CANCELLED" else str(error)
                    )
                else:
                    self._write_report(
                        outcome, failure=None if outcome == "CANCELLED" else str(error)
                    )
            except Exception as report_error:
                error.add_note(f"Processing report also failed: {report_error}")
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
                resolution_change=self.resolution_change,
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
                    self.settings.history_source,
                    self.reset_frames,
                    self.resolution_change,
                    self._completed_numbers,
                    self.effective_settings,
                ),
            )
            return
        self._trail_recovery_index += 1
        if self._trail_recovery_index == len(self._trail_recovery_frames):
            self._trail_recovery_frames = ()
            self._trail_recovery_index = 0
            if self.current_frame > self.frame_end:
                self._is_finished = True

    def _measure(self, name: str, operation: Callable[[], object]) -> object:
        timing = self._active_timing
        if timing is None:
            raise RuntimeError("No active frame timing")
        started = self._timer_ns()
        result = operation()
        timing.stages_ns[name] = self._timer_ns() - started
        return result

    def _process_current_frame(self) -> None:
        frame_number = self.current_frame
        frame = self.paths.frame(frame_number)
        self._active_timing = _FrameTiming(
            frame_number=frame_number,
            reset=None,
            started_ns=self._timer_ns(),
            stages_ns={},
        )
        raw_frame = self.resolved_inputs[frame_number]
        matte_path = self.matte_provider.path_for_frame(frame_number, self.paths)
        if matte_path == frame.matte:
            matte_path = raw_frame.matte
        try:
            beauty = cast(
                np.ndarray,
                self._measure("beauty_read", lambda: self.image_io.read_rgba(raw_frame.beauty)),
            )
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Missing beauty input for frame {frame_number}: {raw_frame.beauty}"
            ) from None
        try:
            motion = cast(
                np.ndarray,
                self._measure("vector_read", lambda: self.image_io.read_rgba(raw_frame.vector)),
            )
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Missing vector input for frame {frame_number}: {raw_frame.vector}"
            ) from None
        try:
            matte = cast(
                np.ndarray,
                self._measure("matte_read", lambda: self.image_io.read_mask(matte_path)),
            )
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
        force_reset = (
            frame_number in (self.frame_start, self._recovery_reset_frame)
            or frame_number in self.reset_frames
            or resolution_changed
        )
        if self._active_timing is not None:
            self._active_timing.reset = force_reset
        processing_result = cast(
            tuple[np.ndarray, FeedbackState, FrameDiagnostics],
            self._measure(
                "core_processing",
                lambda: process_frame_with_diagnostics(
                    beauty,
                    motion,
                    matte,
                    None if resolution_changed else self._state,
                    frame_number,
                    self.settings,
                    force_reset=force_reset,
                ),
            ),
        )
        output, self._state, frame_diagnostics = processing_result
        self._measure(
            "processed_exr_write",
            lambda: self.image_io.write_rgba(frame.processed, output),
        )
        logging.getLogger(__name__).info(
            "Wrote processed frame %d: %s", frame_number, frame.processed
        )
        committed_numbers = [*self._completed_numbers, frame_number]
        self._measure(
            "manifest_commit",
            lambda: _write_manifest(
                self.manifest_path,
                _new_manifest(
                    self.frame_start,
                    self.frame_end,
                    self.settings_fingerprint,
                    self.settings.history_source,
                    self.reset_frames,
                    self.resolution_change,
                    committed_numbers,
                    self.effective_settings,
                ),
            ),
        )
        # Publish completion only after the recovery manifest atomically commits the frame.
        self._completed_numbers = committed_numbers
        self.completed_frames = (*self.completed_frames, frame.processed)
        self._frame_diagnostics.append(frame_diagnostics)
        if frame_number == self.frame_end:
            self._is_finished = True
            self._commit_frame_report("SUCCESS")
            self._log_new_actionable_warning()
        else:
            self.current_frame += 1
            assessment = assess_near_no_op(
                ProcessingDiagnostics.from_frames(tuple(self._frame_diagnostics)), self.settings
            )
            warning_became_actionable = (
                assessment.likely_near_no_op and not self._near_no_op_reported
            )
            reached_checkpoint = (
                len(self._completed_numbers) % _REPORT_CHECKPOINT_INTERVAL_FRAMES == 0
            )
            if reached_checkpoint or warning_became_actionable:
                self._commit_frame_report("RUNNING")
                if warning_became_actionable:
                    self._log_new_actionable_warning()
            else:
                self._finish_frame_timing_without_report("RUNNING")

    def _log_new_actionable_warning(self) -> None:
        """Log an efficacy warning once, at the first boundary where it is actionable."""
        if self._near_no_op_reported:
            return
        warnings = self.advisory_warnings
        if not any(warning.startswith("Likely ineffective feedback:") for warning in warnings):
            return
        for warning in warnings:
            logging.getLogger(__name__).warning("%s; report=%s", warning, self.report_path)
        self._near_no_op_reported = True

    def _finish_frame_timing_without_report(self, outcome: Literal["RUNNING"]) -> None:
        """Finalize timing for a frame whose observational report is intentionally deferred."""
        timing = self._active_timing
        if timing is None:
            return
        timing.total_frame_ns = self._timer_ns() - timing.started_ns
        timing.outcome = outcome
        self._frame_timings.append(timing)
        self._active_timing = None

    def _commit_frame_report(
        self,
        outcome: Literal["SUCCESS", "CANCELLED", "FAILURE", "RUNNING"],
        *,
        failure: str | None = None,
    ) -> None:
        """Commit the report, then publish its observed duration in an atomic refresh."""
        timing = self._active_timing
        if timing is None:
            self._write_report(outcome, failure=failure)
            return
        started = self._timer_ns()
        self._write_report(outcome, failure=failure)
        timing.stages_ns["diagnostics_report_commit"] = self._timer_ns() - started
        timing.total_frame_ns = self._timer_ns() - timing.started_ns
        timing.outcome = outcome
        self._frame_timings.append(timing)
        self._active_timing = None
        self._write_report(outcome, failure=failure)

    def _write_report(
        self,
        outcome: Literal["SUCCESS", "CANCELLED", "FAILURE", "RUNNING"],
        *,
        failure: str | None = None,
    ) -> None:
        diagnostics = ProcessingDiagnostics.from_frames(tuple(self._frame_diagnostics))
        configuration = {
            "history_source": self.settings.history_source.value,
            "mode": self.settings.mode.value,
            "invalid_history_fallback": self.settings.invalid_history_fallback.value,
            "semantic_settings_reference": f"{self.manifest_path.name}#/effective_settings",
            "extension_version": self.effective_settings.get("extension_version"),
            "blender_version": self.effective_settings.get("blender_version"),
        }
        payload = diagnostics.to_report_payload(
            outcome=outcome,
            frame_start=self.frame_start,
            frame_end=self.frame_end,
            completed_frames=tuple(self._completed_numbers),
            configuration=configuration,
            manifest_path=self.manifest_path,
            report_path=self.report_path,
            settings_fingerprint=self.settings_fingerprint,
            warnings=self.advisory_warnings,
            failure=failure,
            checkpoint_interval_frames=_REPORT_CHECKPOINT_INTERVAL_FRAMES,
            active_report_may_lag_manifest=outcome == "RUNNING",
        )
        payload["performance"] = self.performance_timings
        _write_json_atomic(self.report_path, payload, compact=True)


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
    extension_version: str | None = None,
    blender_version: str | None = None,
    timer_ns: Callable[[], int] = time.perf_counter_ns,
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
            extension_version=extension_version,
            blender_version=blender_version,
            timer_ns=timer_ns,
        )
    except Exception as error:
        if frame_error_factory is None:
            raise
        raise frame_error_factory(frame_start, error) from error

    def write_external_failure_report(error: Exception) -> None:
        """Record orchestration failures that occur outside ``process_next_frame``."""
        try:
            session.write_terminal_report("FAILURE", failure=str(error))
        except Exception as report_error:
            error.add_note(f"Processing report also failed: {report_error}")

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
                write_external_failure_report(error)
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
                    write_external_failure_report(error)
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
            write_external_failure_report(error)
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
    resolution_change: ResolutionChangePolicy,
) -> FeedbackState:
    """Rebuild one frame of trail coverage while trusting processed color as history."""
    frame = paths.frame(frame_number)
    history = image_io.read_rgba(frame.processed)
    matte = image_io.read_mask(matte_provider.path_for_frame(frame_number, paths))
    dimensions_changed = state is not None and state.history.shape != history.shape
    if dimensions_changed and resolution_change is ResolutionChangePolicy.ERROR:
        raise ValueError(
            f"Resolution changed in resume history at frame {frame_number}: "
            f"{state.history.shape[:2]} -> {history.shape[:2]}"
        )
    reset = state is None or frame_number in reset_frames or dimensions_changed
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


def _stable_value(value: object) -> object:
    """Convert a processing value to deterministic, human-readable JSON data."""
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (tuple, list, frozenset, set)):
        converted = [_stable_value(item) for item in value]
        return sorted(converted) if isinstance(value, (frozenset, set)) else converted
    if isinstance(value, dict):
        return {str(key): _stable_value(item) for key, item in sorted(value.items())}
    raise TypeError(f"Unsupported processing setting value: {type(value).__name__}")


def _matte_provider_snapshot(matte_provider: MatteProvider) -> dict[str, object]:
    provider_settings: dict[str, object] = {}
    if is_dataclass(matte_provider):
        provider_settings = {
            field.name: _stable_value(getattr(matte_provider, field.name))
            for field in fields(matte_provider)
        }
    return {"type": type(matte_provider).__name__, "settings": provider_settings}


def _semantic_settings_snapshot(
    settings: FeedbackSettings, matte_provider: MatteProvider
) -> dict[str, object]:
    """Serialize every feedback field and matte-provider setting without a field list."""
    payload = {
        field.name: _stable_value(getattr(settings, field.name)) for field in fields(settings)
    }
    payload["matte_provider"] = _matte_provider_snapshot(matte_provider)
    return payload


def _effective_settings_snapshot(
    settings: FeedbackSettings,
    matte_provider: MatteProvider,
    *,
    reset_frames: frozenset[int],
    resolution_change: ResolutionChangePolicy,
    extension_version: str | None,
    blender_version: str | None,
) -> dict[str, object]:
    snapshot = _semantic_settings_snapshot(settings, matte_provider)
    snapshot.update(
        {
            "reset_frames": sorted(reset_frames),
            "resolution_change": resolution_change.value,
            "extension_version": extension_version or "unavailable",
            "blender_version": blender_version or "unavailable",
        }
    )
    return snapshot


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _settings_fingerprint(settings: FeedbackSettings, matte_provider: MatteProvider) -> str:
    payload = _semantic_settings_snapshot(settings, matte_provider)
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _new_manifest(
    frame_start: int,
    frame_end: int,
    fingerprint: str,
    history_source: HistorySource,
    reset_frames: frozenset[int],
    resolution_change: ResolutionChangePolicy,
    completed: list[int],
    effective_settings: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": _MANIFEST_VERSION,
        "image_orientation": _IMAGE_ORIENTATION,
        "frame_start": frame_start,
        "frame_end": frame_end,
        "history_source": history_source.value,
        "settings_fingerprint": fingerprint,
        "effective_settings": effective_settings,
        "reset_frames": sorted(reset_frames),
        "resolution_change": resolution_change.value,
        "completed_frames": completed,
    }


def _write_json_atomic(path: Path, payload: dict[str, object], *, compact: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    serialized = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if compact
        else json.dumps(payload, indent=2, sort_keys=True)
    )
    temporary.write_text(serialized + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_manifest(path: Path, manifest: dict[str, object]) -> None:
    _write_json_atomic(path, manifest, compact=False)


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
    history_source: HistorySource,
    reset_frames: frozenset[int],
    resolution_change: ResolutionChangePolicy,
    semantic_settings: dict[str, object],
) -> None:
    schema_version = manifest.get("schema_version")
    if schema_version == 2:
        raise ValueError(
            "Sequence recovery manifest schema 2 cannot prove the complete effective settings; "
            "reprocess the sequence (retained raw passes remain reusable)"
        )
    expected = {
        "schema_version": _MANIFEST_VERSION,
        "image_orientation": _IMAGE_ORIENTATION,
        "frame_start": frame_start,
        "frame_end": frame_end,
        "history_source": history_source.value,
        "settings_fingerprint": fingerprint,
        "reset_frames": sorted(reset_frames),
        "resolution_change": resolution_change.value,
    }
    for name, value in expected.items():
        if _canonical_json(manifest.get(name)) != _canonical_json(value):
            raise ValueError(f"Sequence recovery manifest is incompatible: {name} changed")
    effective_settings = manifest.get("effective_settings")
    if not isinstance(effective_settings, dict):
        raise ValueError(
            "Sequence recovery manifest is incompatible: effective_settings is missing or invalid"
        )
    if effective_settings.get("history_source") != manifest.get("history_source"):
        raise ValueError(
            "Sequence recovery manifest is incompatible: history_source disagrees with "
            "effective_settings"
        )
    expected_effective_names = {
        *semantic_settings,
        "reset_frames",
        "resolution_change",
        "extension_version",
        "blender_version",
    }
    if set(effective_settings) != expected_effective_names:
        raise ValueError(
            "Sequence recovery manifest is incompatible: effective_settings fields changed"
        )
    for name, value in semantic_settings.items():
        if _canonical_json(effective_settings.get(name)) != _canonical_json(value):
            raise ValueError(
                f"Sequence recovery manifest is incompatible: effective_settings.{name} changed"
            )
    if _canonical_json(effective_settings.get("reset_frames")) != _canonical_json(
        sorted(reset_frames)
    ):
        raise ValueError(
            "Sequence recovery manifest is incompatible: effective_settings.reset_frames changed"
        )
    if effective_settings.get("resolution_change") != resolution_change.value:
        raise ValueError(
            "Sequence recovery manifest is incompatible: "
            "effective_settings.resolution_change changed"
        )
    for name in ("extension_version", "blender_version"):
        provenance = effective_settings.get(name)
        if not isinstance(provenance, str) or not provenance:
            raise ValueError(
                f"Sequence recovery manifest is incompatible: effective_settings.{name} "
                "is missing or invalid"
            )
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
