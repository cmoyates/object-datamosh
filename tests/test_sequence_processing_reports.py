import json
import logging
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

import object_datamosh.sequence_processing as sequence_processing
from object_datamosh.core.contracts import FeedbackMode, FeedbackSettings
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


@pytest.mark.parametrize(
    ("completed_before_read", "expected_report_count"),
    ((1, 0), (9, 0), (10, 10), (20, 20)),
)
def test_running_report_covers_short_runs_and_exact_and_multiple_checkpoints(
    tmp_path: Path,
    completed_before_read: int,
    expected_report_count: int,
) -> None:
    paths = SequencePaths(tmp_path)
    session = ProcessingSession.create(
        paths,
        frame_start=1,
        frame_end=21,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(block_size=1),
        image_io=MemoryImageIO(_inputs(paths, 21)),
    )

    for _ in range(completed_before_read):
        session.process_next_frame()

    assert _report(paths)["manifest_completed_prefix"]["count"] == expected_report_count


def test_running_report_checkpoints_every_ten_completed_frames(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    session = ProcessingSession.create(
        paths,
        frame_start=1,
        frame_end=12,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(block_size=1),
        image_io=MemoryImageIO(_inputs(paths, 12)),
    )

    for _ in range(9):
        session.process_next_frame()

    active_report = _report(paths)
    manifest = json.loads(sequence_manifest_path(paths).read_text(encoding="utf-8"))
    assert manifest["completed_frames"] == list(range(1, 10))
    assert active_report["manifest_completed_prefix"] == {
        "count": 0,
        "start": None,
        "end": None,
    }
    assert active_report["diagnostics_completed_prefix"] == {
        "count": 0,
        "start": None,
        "end": None,
    }
    assert active_report["active_report_may_lag_manifest"] is True
    assert active_report["checkpoint_interval_frames"] == 10
    assert active_report["report_lag"] == {
        "manifest_prefix_observed_at_report_write": {
            "count": 0,
            "start": None,
            "end": None,
        },
        "diagnostics_prefix_in_report": {"count": 0, "start": None, "end": None},
        "manifest_is_authoritative": True,
        "policy": "active_report_may_lag_by_up_to_checkpoint_interval_minus_one_frames",
        "manifest_observation_lag_at_report_write": 0,
        "maximum_manifest_observation_lag_while_active": 9,
        "diagnostics_lag_at_report_write": 0,
        "maximum_diagnostics_lag_while_active": 9,
    }

    session.process_next_frame()

    checkpoint = _report(paths)
    assert checkpoint["manifest_completed_prefix"] == {"count": 10, "start": 1, "end": 10}
    assert checkpoint["diagnostics_completed_prefix"] == {
        "count": 10,
        "start": 1,
        "end": 10,
    }


def test_first_actionable_near_no_op_warning_forces_an_early_checkpoint(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    paths = SequencePaths(tmp_path)
    inputs = _inputs(paths, 5)
    for number in range(2, 6):
        inputs[paths.frame(number).vector].fill(10_000.0)
    session = ProcessingSession.create(
        paths,
        frame_start=1,
        frame_end=5,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(block_size=1),
        image_io=MemoryImageIO(inputs),
    )

    session.process_next_frame()
    session.process_next_frame()
    assert _report(paths)["manifest_completed_prefix"]["count"] == 0

    with caplog.at_level(logging.WARNING):
        session.process_next_frame()
        warning_report = _report(paths)
        assert warning_report["manifest_completed_prefix"] == {
            "count": 3,
            "start": 1,
            "end": 3,
        }
        while not session.is_finished:
            session.process_next_frame()

    terminal_report = _report(paths)
    assert terminal_report["manifest_completed_prefix"] == {"count": 5, "start": 1, "end": 5}
    assert terminal_report["diagnostics_completed_prefix"] == {
        "count": 5,
        "start": 1,
        "end": 5,
    }
    assert terminal_report["warnings"]
    warning_records = [
        record for record in caplog.records if "Likely ineffective feedback:" in record.message
    ]
    assert len(warning_records) == 1


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
    assert cancelled["manifest_completed_prefix"] == cancelled["completed_prefix"]
    assert cancelled["diagnostics_completed_prefix"] == cancelled["completed_prefix"]
    assert cancelled["active_report_may_lag_manifest"] is False

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
    assert failed["manifest_completed_prefix"] == failed["completed_prefix"]
    assert failed["diagnostics_completed_prefix"] == failed["completed_prefix"]
    assert failed["active_report_may_lag_manifest"] is False
    assert [frame["frame_number"] for frame in failed["frames"]] == [1]
    assert "Missing beauty input for frame 2" in failed["failure"]


def test_resume_without_an_older_report_marks_diagnostics_unavailable(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    settings = FeedbackSettings()
    image_io = MemoryImageIO(_inputs(paths, 21))
    first_session = ProcessingSession.create(
        paths,
        frame_start=1,
        frame_end=21,
        matte_provider=ObjectIndexMatteProvider(),
        settings=settings,
        image_io=image_io,
    )
    for _ in range(20):
        first_session.process_next_frame()
    processing_report_path(paths).unlink()

    ProcessingSession.create(
        paths,
        frame_start=1,
        frame_end=21,
        matte_provider=ObjectIndexMatteProvider(),
        settings=settings,
        image_io=image_io,
        run_mode=SequenceRunMode.RESUME,
    )

    report = _report(paths)
    assert report["completed_prefix"] == {"count": 20, "start": 1, "end": 20}
    assert report["diagnostics_availability"] == "UNAVAILABLE"
    assert report["diagnostics_completed_prefix"] == {"count": 0, "start": None, "end": None}
    assert report["report_lag"]["manifest_observation_lag_at_report_write"] == 0
    assert report["report_lag"]["maximum_manifest_observation_lag_while_active"] == 9
    assert report["report_lag"]["diagnostics_lag_at_report_write"] == 20
    assert report["report_lag"]["maximum_diagnostics_lag_while_active"] == 29
    assert report["report_lag"]["policy"] == (
        "active_report_may_checkpoint_lag_manifest_and_prior_resume_diagnostics_are_unavailable"
    )


def test_fully_completed_trail_resume_rewrites_running_report_as_terminal_success(
    tmp_path: Path,
) -> None:
    paths = SequencePaths(tmp_path)
    settings = FeedbackSettings(mode=FeedbackMode.TRAIL, block_size=1)
    image_io = MemoryImageIO(_inputs(paths, 3))
    process_sequence(
        paths,
        frame_start=1,
        frame_end=3,
        matte_provider=ObjectIndexMatteProvider(),
        settings=settings,
        image_io=image_io,
    )

    resumed = ProcessingSession.create(
        paths,
        frame_start=1,
        frame_end=3,
        matte_provider=ObjectIndexMatteProvider(),
        settings=settings,
        image_io=image_io,
        run_mode=SequenceRunMode.RESUME,
    )
    assert _report(paths)["terminal_outcome"] == "RUNNING"

    while not resumed.is_finished:
        resumed.process_next_frame()

    report = _report(paths)
    assert resumed.result.frames == ()
    assert report["terminal_outcome"] == "SUCCESS"
    assert report["manifest_completed_prefix"] == {"count": 3, "start": 1, "end": 3}
    assert report["diagnostics_availability"] == "UNAVAILABLE"
    assert report["active_report_may_lag_manifest"] is False
    assert report["report_lag"]["policy"] == (
        "terminal_report_contains_all_in_memory_diagnostics_but_"
        "prior_resume_diagnostics_are_unavailable"
    )


def test_resume_from_report_lag_preserves_exact_outputs_and_writes_complete_terminal_diagnostics(
    tmp_path: Path,
) -> None:
    uninterrupted_paths = SequencePaths(tmp_path / "uninterrupted")
    resumed_paths = SequencePaths(tmp_path / "resumed")
    settings = FeedbackSettings(block_size=1, persistence=0.75)
    uninterrupted_io = MemoryImageIO(_inputs(uninterrupted_paths, 12))
    resumed_io = MemoryImageIO(_inputs(resumed_paths, 12))

    process_sequence(
        uninterrupted_paths,
        frame_start=1,
        frame_end=12,
        matte_provider=ObjectIndexMatteProvider(),
        settings=settings,
        image_io=uninterrupted_io,
    )
    interrupted = ProcessingSession.create(
        resumed_paths,
        frame_start=1,
        frame_end=12,
        matte_provider=ObjectIndexMatteProvider(),
        settings=settings,
        image_io=resumed_io,
    )
    for _ in range(9):
        interrupted.process_next_frame()
    assert _report(resumed_paths)["manifest_completed_prefix"]["count"] == 0

    resumed = ProcessingSession.create(
        resumed_paths,
        frame_start=1,
        frame_end=12,
        matte_provider=ObjectIndexMatteProvider(),
        settings=settings,
        image_io=resumed_io,
        run_mode=SequenceRunMode.RESUME,
    )
    while not resumed.is_finished:
        resumed.process_next_frame()

    for number in range(1, 13):
        np.testing.assert_array_equal(
            resumed_io.images[resumed_paths.frame(number).processed],
            uninterrupted_io.images[uninterrupted_paths.frame(number).processed],
        )
    report = _report(resumed_paths)
    assert report["terminal_outcome"] == "SUCCESS"
    assert report["manifest_completed_prefix"] == {"count": 12, "start": 1, "end": 12}
    assert report["diagnostics_completed_prefix"] == {"count": 3, "start": 10, "end": 12}
    assert report["diagnostics_availability"] == "PARTIAL"
    assert [frame["frame_number"] for frame in report["frames"]] == [10, 11, 12]
    assert report["active_report_may_lag_manifest"] is False
    assert report["report_lag"]["manifest_observation_lag_at_report_write"] == 0
    assert report["report_lag"]["maximum_manifest_observation_lag_while_active"] == 0
    assert report["report_lag"]["diagnostics_lag_at_report_write"] == 9
    assert report["report_lag"]["maximum_diagnostics_lag_while_active"] == 9
    assert report["report_lag"]["policy"] == (
        "terminal_report_contains_all_in_memory_diagnostics_but_prior_resume_diagnostics_are_unavailable"
    )


def test_manifest_commits_atomically_after_every_frame_while_reports_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SequencePaths(tmp_path)
    manifest_snapshots: list[list[int]] = []
    report_write_count = 0
    original_replace = sequence_processing.os.replace

    def observe_replace(source: str | Path, destination: str | Path) -> None:
        nonlocal report_write_count
        destination_path = Path(destination)
        if destination_path == sequence_manifest_path(paths):
            payload = json.loads(Path(source).read_text(encoding="utf-8"))
            manifest_snapshots.append(payload["completed_frames"])
        elif destination_path == processing_report_path(paths):
            report_write_count += 1
        original_replace(source, destination)

    monkeypatch.setattr(sequence_processing.os, "replace", observe_replace)
    session = ProcessingSession.create(
        paths,
        frame_start=1,
        frame_end=12,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(block_size=1),
        image_io=MemoryImageIO(_inputs(paths, 12)),
    )
    while not session.is_finished:
        session.process_next_frame()

    assert manifest_snapshots == [list(range(1, end + 1)) for end in range(0, 13)]
    assert report_write_count == 5  # start, two checkpoint refreshes, two terminal refreshes


def test_initial_report_write_failure_leaves_the_atomic_empty_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SequencePaths(tmp_path)
    original_replace = sequence_processing.os.replace

    def fail_initial_report(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == processing_report_path(paths):
            raise OSError("initial report replace failed")
        original_replace(source, destination)

    monkeypatch.setattr(sequence_processing.os, "replace", fail_initial_report)
    with pytest.raises(
        RuntimeError,
        match=(
            "Diagnostics report write failed during session initialization: "
            "initial report replace failed"
        ),
    ) as raised:
        ProcessingSession.create(
            paths,
            frame_start=1,
            frame_end=2,
            matte_provider=ObjectIndexMatteProvider(),
            settings=FeedbackSettings(),
            image_io=MemoryImageIO(_inputs(paths, 2)),
        )

    assert isinstance(raised.value.__cause__, OSError)
    manifest = json.loads(sequence_manifest_path(paths).read_text(encoding="utf-8"))
    assert manifest["completed_frames"] == []
    assert not processing_report_path(paths).exists()


def test_report_write_failure_keeps_completed_manifest_and_prior_atomic_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SequencePaths(tmp_path)
    session = ProcessingSession.create(
        paths,
        frame_start=1,
        frame_end=10,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(block_size=1),
        image_io=MemoryImageIO(_inputs(paths, 10)),
    )
    initial = processing_report_path(paths).read_bytes()
    for _ in range(9):
        session.process_next_frame()
    original_replace = sequence_processing.os.replace

    def fail_report_replace(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == processing_report_path(paths):
            raise OSError("report replace failed")
        original_replace(source, destination)

    monkeypatch.setattr(sequence_processing.os, "replace", fail_report_replace)
    with pytest.raises(OSError, match="report replace failed") as raised:
        session.process_next_frame()

    manifest = json.loads(sequence_manifest_path(paths).read_text(encoding="utf-8"))
    assert manifest["completed_frames"] == list(range(1, 11))
    assert processing_report_path(paths).read_bytes() == initial
    assert any("Processing report also failed" in note for note in raised.value.__notes__)

    monkeypatch.setattr(sequence_processing.os, "replace", original_replace)
    session.write_terminal_report("FAILURE", failure="report checkpoint failed")
    terminal = _report(paths)
    assert terminal["manifest_completed_prefix"] == {"count": 10, "start": 1, "end": 10}
    assert terminal["diagnostics_completed_prefix"] == terminal["manifest_completed_prefix"]
    assert terminal["failure"] == "report checkpoint failed"


def test_explicit_terminal_report_flushes_all_diagnostics_since_the_last_checkpoint(
    tmp_path: Path,
) -> None:
    paths = SequencePaths(tmp_path)
    session = ProcessingSession.create(
        paths,
        frame_start=1,
        frame_end=9,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(block_size=1),
        image_io=MemoryImageIO(_inputs(paths, 9)),
    )
    for _ in range(8):
        session.process_next_frame()

    session.write_terminal_report("CANCELLED")

    report = _report(paths)
    assert report["terminal_outcome"] == "CANCELLED"
    assert report["manifest_completed_prefix"] == {"count": 8, "start": 1, "end": 8}
    assert report["diagnostics_completed_prefix"] == report["manifest_completed_prefix"]
    assert len(report["frames"]) == 8
    assert report["active_report_may_lag_manifest"] is False


@pytest.mark.parametrize("failing_method", ("begin", "update", "end"))
def test_synchronous_progress_errors_write_complete_terminal_failure_reports(
    tmp_path: Path, failing_method: str
) -> None:
    paths = SequencePaths(tmp_path / failing_method)

    class FailingProgress:
        def begin(self, total: int) -> None:
            if failing_method == "begin":
                raise RuntimeError("progress begin failed")

        def update(self, completed: int) -> None:
            if failing_method == "update":
                raise RuntimeError("progress update failed")

        def end(self) -> None:
            if failing_method == "end":
                raise RuntimeError("progress end failed")

    with pytest.raises(RuntimeError, match=f"progress {failing_method} failed"):
        process_sequence(
            paths,
            frame_start=1,
            frame_end=1,
            matte_provider=ObjectIndexMatteProvider(),
            settings=FeedbackSettings(block_size=1),
            image_io=MemoryImageIO(_inputs(paths, 1)),
            progress=FailingProgress(),
        )

    report = _report(paths)
    assert report["terminal_outcome"] == "FAILURE"
    expected_count = 0 if failing_method == "begin" else 1
    assert report["manifest_completed_prefix"]["count"] == expected_count
    assert report["diagnostics_completed_prefix"]["count"] == expected_count
    assert report["active_report_may_lag_manifest"] is False
    assert report["failure"] == f"progress {failing_method} failed"


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
