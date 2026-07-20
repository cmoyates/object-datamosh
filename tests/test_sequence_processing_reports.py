import json
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

import object_datamosh.sequence_processing as sequence_processing
from object_datamosh.core.contracts import FeedbackSettings
from object_datamosh.core.mattes import ObjectIndexMatteProvider
from object_datamosh.core.paths import SequencePaths
from object_datamosh.sequence_processing import (
    ProcessingSession,
    SequenceProcessingCancelled,
    SequenceRunMode,
    process_sequence,
    processing_report_path,
    sequence_manifest_path,
)


class MemoryImageIO:
    def __init__(self, images: dict[Path, np.ndarray]) -> None:
        self.images = images

    def read_rgba(self, path: str | Path) -> np.ndarray:
        try:
            return self.images[Path(path)].copy()
        except KeyError:
            raise FileNotFoundError(path) from None

    def read_mask(self, path: str | Path) -> np.ndarray:
        return self.read_rgba(path)

    def write_rgba(self, path: str | Path, pixels: np.ndarray) -> None:
        resolved = Path(path)
        self.images[resolved] = pixels.copy()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.touch()


def _inputs(paths: SequencePaths, count: int) -> dict[Path, np.ndarray]:
    images: dict[Path, np.ndarray] = {}
    for number in range(1, count + 1):
        frame = paths.frame(number)
        images[frame.beauty] = np.full((1, 2, 4), number / 10, dtype=np.float32)
        images[frame.vector] = np.zeros((1, 2, 4), dtype=np.float32)
        images[frame.matte] = np.ones((1, 2), dtype=np.float32)
    return images


def _report(paths: SequencePaths) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(processing_report_path(paths).read_text(encoding="utf-8")),
    )


def test_success_report_agrees_with_manifest_and_contains_actual_frame_counters(
    tmp_path: Path,
) -> None:
    paths = SequencePaths(tmp_path)
    process_sequence(
        paths,
        frame_start=1,
        frame_end=2,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(block_size=1),
        image_io=MemoryImageIO(_inputs(paths, 2)),
    )

    report = _report(paths)
    manifest = json.loads(sequence_manifest_path(paths).read_text(encoding="utf-8"))
    assert report["terminal_outcome"] == "SUCCESS"
    assert report["completed_prefix"] == {"count": 2, "start": 1, "end": 2}
    assert report["agreement"]["completed_prefix"] == report["completed_prefix"]
    assert report["agreement"]["history_source"] == manifest["history_source"]
    assert report["agreement"]["settings_fingerprint"] == manifest["settings_fingerprint"]
    assert report["configuration"]["semantic_settings_reference"] == (
        "ODM_sequence_manifest.json#/effective_settings"
    )
    assert report["frames"][0]["reset"] is True
    assert report["frames"][1]["primary_history_attempts"] == 2


def test_cancelled_and_failed_reports_preserve_only_completed_prefix(tmp_path: Path) -> None:
    cancelled_paths = SequencePaths(tmp_path / "cancelled")
    cancel_calls = 0

    def cancel_after_first() -> bool:
        nonlocal cancel_calls
        cancel_calls += 1
        return cancel_calls > 1

    session = ProcessingSession.create(
        cancelled_paths,
        frame_start=1,
        frame_end=3,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(),
        image_io=MemoryImageIO(_inputs(cancelled_paths, 3)),
        should_cancel=cancel_after_first,
    )
    session.process_next_frame()
    with pytest.raises(SequenceProcessingCancelled):
        session.process_next_frame()
    cancelled = _report(cancelled_paths)
    assert cancelled["terminal_outcome"] == "CANCELLED"
    assert cancelled["completed_prefix"] == {"count": 1, "start": 1, "end": 1}

    failed_paths = SequencePaths(tmp_path / "failed")
    failed_session = ProcessingSession.create(
        failed_paths,
        frame_start=1,
        frame_end=2,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(),
        image_io=MemoryImageIO(_inputs(failed_paths, 1)),
    )
    failed_session.process_next_frame()
    with pytest.raises(FileNotFoundError, match="frame 2"):
        failed_session.process_next_frame()
    failed = _report(failed_paths)
    assert failed["terminal_outcome"] == "FAILURE"
    assert failed["completed_prefix"] == {"count": 1, "start": 1, "end": 1}
    assert "Missing beauty input for frame 2" in failed["failure"]


def test_resume_without_an_older_report_marks_diagnostics_unavailable(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    settings = FeedbackSettings()
    image_io = MemoryImageIO(_inputs(paths, 2))
    first_session = ProcessingSession.create(
        paths,
        frame_start=1,
        frame_end=2,
        matte_provider=ObjectIndexMatteProvider(),
        settings=settings,
        image_io=image_io,
    )
    first_session.process_next_frame()
    processing_report_path(paths).unlink()

    ProcessingSession.create(
        paths,
        frame_start=1,
        frame_end=2,
        matte_provider=ObjectIndexMatteProvider(),
        settings=settings,
        image_io=image_io,
        run_mode=SequenceRunMode.RESUME,
    )

    report = _report(paths)
    assert report["completed_prefix"] == {"count": 1, "start": 1, "end": 1}
    assert report["diagnostics_availability"] == "UNAVAILABLE"
    assert report["diagnostics_completed_prefix"] == {"count": 0, "start": None, "end": None}


def test_report_replacement_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = SequencePaths(tmp_path)
    session = ProcessingSession.create(
        paths,
        frame_start=1,
        frame_end=1,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(),
        image_io=MemoryImageIO(_inputs(paths, 1)),
    )
    initial = processing_report_path(paths).read_bytes()
    original_replace = sequence_processing.os.replace

    def fail_report_replace(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == processing_report_path(paths):
            raise OSError("report replace failed")
        original_replace(source, destination)

    monkeypatch.setattr(sequence_processing.os, "replace", fail_report_replace)
    with pytest.raises(OSError, match="report replace failed"):
        session.process_next_frame()

    assert processing_report_path(paths).read_bytes() == initial
