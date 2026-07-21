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
    MAX_REPORTED_TIMING_FRAMES,
    ProcessingSession,
    SequenceRunMode,
    processing_report_path,
    sequence_manifest_path,
)


class SteppingTimer:
    def __init__(self, step: int = 10) -> None:
        self.value = 0
        self.step = step

    def __call__(self) -> int:
        value = self.value
        self.value += self.step
        return value


class ObservableImageIO:
    def __init__(self, images: dict[Path, np.ndarray], fail: str | None = None) -> None:
        self.images = images
        self.fail = fail

    def read_rgba(self, path: str | Path) -> np.ndarray:
        resolved = Path(path)
        if self.fail == resolved.name:
            raise RuntimeError(f"failed {resolved.name}")
        return self.images[resolved].copy()

    def read_mask(self, path: str | Path) -> np.ndarray:
        resolved = Path(path)
        if self.fail == "matte":
            raise RuntimeError("failed matte")
        return self.images[resolved].copy()

    def write_rgba(self, path: str | Path, pixels: np.ndarray) -> None:
        if self.fail == "write":
            raise RuntimeError("failed write")
        resolved = Path(path)
        self.images[resolved] = pixels.copy()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.touch()


def _images(paths: SequencePaths, count: int = 1) -> dict[Path, np.ndarray]:
    images: dict[Path, np.ndarray] = {}
    for number in range(1, count + 1):
        frame = paths.frame(number)
        images[frame.beauty] = np.full((1, 2, 4), number / 10, dtype=np.float32)
        images[frame.vector] = np.zeros((1, 2, 4), dtype=np.float32)
        images[frame.matte] = np.ones((1, 2), dtype=np.float32)
    return images


def _session(
    paths: SequencePaths,
    image_io: ObservableImageIO,
    timer: SteppingTimer,
    *,
    frame_end: int = 1,
) -> ProcessingSession:
    return ProcessingSession.create(
        paths,
        frame_start=1,
        frame_end=frame_end,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(block_size=1),
        image_io=image_io,
        timer_ns=timer,
    )


def _performance(paths: SequencePaths) -> dict[str, Any]:
    report = cast(
        dict[str, Any],
        json.loads(processing_report_path(paths).read_text(encoding="utf-8")),
    )
    return cast(dict[str, Any], report["performance"])


def test_successful_reset_frame_accounts_for_completed_stages_in_order(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    session = _session(paths, ObservableImageIO(_images(paths)), SteppingTimer())

    session.process_next_frame()

    timing = _performance(paths)["frames"][0]
    assert timing["frame_number"] == 1
    assert timing["reset"] is True
    assert timing["stage_order"] == [
        "beauty_read",
        "vector_read",
        "matte_read",
        "core_processing",
        "processed_exr_write",
        "manifest_commit",
        "diagnostics_report_commit",
    ]
    assert set(timing["stages_ns"].values()) == {10}
    assert timing["total_frame_ns"] >= sum(timing["stages_ns"].values())
    performance = _performance(paths)
    assert performance["schema_version"] == 1
    assert performance["clock"] == "perf_counter_ns"
    assert performance["unit"] == "nanoseconds"
    assert performance["observational_only"] is True
    assert performance["history_limit"] == MAX_REPORTED_TIMING_FRAMES


def test_non_reset_frame_timing_does_not_change_output_or_semantic_diagnostics(
    tmp_path: Path,
) -> None:
    paths = SequencePaths(tmp_path)
    images = _images(paths, 2)
    session = _session(paths, ObservableImageIO(images), SteppingTimer(), frame_end=2)

    session.process_next_frame()
    first_fingerprint = json.loads(sequence_manifest_path(paths).read_text(encoding="utf-8"))[
        "settings_fingerprint"
    ]
    session.process_next_frame()

    report = json.loads(processing_report_path(paths).read_text(encoding="utf-8"))
    assert report["frames"][1]["reset"] is False
    assert report["performance"]["frames"][1]["reset"] is False
    assert report["performance"]["frames"][1]["frame_number"] == 2
    assert (
        json.loads(sequence_manifest_path(paths).read_text(encoding="utf-8"))[
            "settings_fingerprint"
        ]
        == first_fingerprint
    )
    np.testing.assert_allclose(
        images[paths.frame(2).processed],
        np.full((1, 2, 4), 0.115, dtype=np.float32),
    )


@pytest.mark.parametrize(
    ("failure", "completed_stages"),
    [
        ("ODM_beauty_0001.exr", ()),
        ("ODM_vector_0001.exr", ("beauty_read",)),
        ("matte", ("beauty_read", "vector_read")),
        (
            "processing",
            ("beauty_read", "vector_read", "matte_read"),
        ),
        (
            "write",
            ("beauty_read", "vector_read", "matte_read", "core_processing"),
        ),
        (
            "manifest",
            (
                "beauty_read",
                "vector_read",
                "matte_read",
                "core_processing",
                "processed_exr_write",
            ),
        ),
    ],
)
def test_failure_timing_never_invents_uncompleted_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    completed_stages: tuple[str, ...],
) -> None:
    paths = SequencePaths(tmp_path)
    io = ObservableImageIO(_images(paths), fail=failure if failure != "processing" else None)
    session = _session(paths, io, SteppingTimer())
    if failure == "processing":
        monkeypatch.setattr(
            sequence_processing,
            "process_frame_with_diagnostics",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("failed processing")),
        )
    if failure == "manifest":
        original_replace = sequence_processing.os.replace

        def fail_manifest(source: str | Path, destination: str | Path) -> None:
            if Path(destination) == sequence_manifest_path(paths):
                raise OSError("failed manifest")
            original_replace(source, destination)

        monkeypatch.setattr(sequence_processing.os, "replace", fail_manifest)

    with pytest.raises((RuntimeError, OSError, KeyError)):
        session.process_next_frame()

    timing = _performance(paths)["frames"][-1]
    assert timing["outcome"] == "FAILURE"
    assert tuple(timing["stage_order"]) == (*completed_stages, "diagnostics_report_commit")
    assert set(timing["stages_ns"]) - {"diagnostics_report_commit"} == set(completed_stages)


def test_report_failure_remains_observable_without_claiming_a_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SequencePaths(tmp_path)
    session = _session(paths, ObservableImageIO(_images(paths)), SteppingTimer())
    original_replace = sequence_processing.os.replace

    def fail_report(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == processing_report_path(paths):
            raise OSError("failed report")
        original_replace(source, destination)

    monkeypatch.setattr(sequence_processing.os, "replace", fail_report)
    with pytest.raises(OSError, match="failed report"):
        session.process_next_frame()

    active = cast(dict[str, Any], session.performance_timings["active_frame"])
    assert "manifest_commit" in active["stages_ns"]
    assert "diagnostics_report_commit" not in active["stages_ns"]


def test_timer_metadata_does_not_change_fingerprint_or_resume_compatibility(
    tmp_path: Path,
) -> None:
    paths = SequencePaths(tmp_path)
    image_io = ObservableImageIO(_images(paths))
    original = _session(paths, image_io, SteppingTimer(step=10))
    original.process_next_frame()
    fingerprint = original.settings_fingerprint

    resumed = ProcessingSession.create(
        paths,
        frame_start=1,
        frame_end=1,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(block_size=1),
        image_io=image_io,
        run_mode=SequenceRunMode.RESUME,
        timer_ns=SteppingTimer(step=1_000_000),
    )

    assert resumed.is_finished
    assert resumed.settings_fingerprint == fingerprint
    assert (
        json.loads(sequence_manifest_path(paths).read_text(encoding="utf-8"))[
            "settings_fingerprint"
        ]
        == fingerprint
    )


def test_timing_history_is_bounded(tmp_path: Path) -> None:
    count = MAX_REPORTED_TIMING_FRAMES + 2
    paths = SequencePaths(tmp_path)
    session = _session(
        paths,
        ObservableImageIO(_images(paths, count)),
        SteppingTimer(),
        frame_end=count,
    )
    while not session.is_finished:
        session.process_next_frame()

    performance = _performance(paths)
    assert len(performance["frames"]) == MAX_REPORTED_TIMING_FRAMES
    assert performance["frames_omitted"] == 2
    assert performance["frames"][0]["frame_number"] == 3
    assert performance["largest_stages"][0]["duration_ns"] >= 0
