import json
from pathlib import Path

import numpy as np
import pytest

from object_datamosh.core.contracts import FeedbackMode, FeedbackSettings
from object_datamosh.core.mattes import ObjectIndexMatteProvider
from object_datamosh.core.paths import FramePaths, SequencePaths
from object_datamosh.sequence_processing import (
    MissingHistoryPolicy,
    ProcessingSession,
    ResolutionChangePolicy,
    SequenceProcessingCancelled,
    SequenceRunMode,
    parse_reset_frames,
    process_sequence,
    sequence_manifest_path,
)


class ProgressRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, int]] = []

    def begin(self, total: int) -> None:
        self.events.append(("begin", total))

    def update(self, completed: int) -> None:
        self.events.append(("update", completed))

    def end(self) -> None:
        self.events.append(("end", 0))


class MemoryImageIO:
    """Image I/O boundary double with observable written outputs."""

    def __init__(self, images: dict[Path, np.ndarray]) -> None:
        self.images = images
        self.written: dict[Path, np.ndarray] = {}
        self.reads: list[Path] = []

    def read_rgba(self, path: str | Path) -> np.ndarray:
        self.reads.append(Path(path))
        try:
            return self.images[Path(path)].copy()
        except KeyError:
            raise FileNotFoundError(path) from None

    def read_mask(self, path: str | Path) -> np.ndarray:
        self.reads.append(Path(path))
        try:
            return self.images[Path(path)].copy()
        except KeyError:
            raise FileNotFoundError(path) from None

    def write_rgba(self, path: str | Path, pixels: np.ndarray) -> None:
        resolved = Path(path)
        self.written[resolved] = pixels.copy()
        self.images[resolved] = pixels.copy()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.touch()


def _rgba(value: float) -> np.ndarray:
    return np.full((1, 2, 4), value, dtype=np.float32)


def test_reset_expression_is_parsed_deterministically() -> None:
    assert parse_reset_frames(" 8, 3,8, 5 ") == frozenset({3, 5, 8})


def test_process_sequence_rejects_an_inverted_frame_range(tmp_path: Path) -> None:
    try:
        process_sequence(
            SequencePaths(tmp_path),
            frame_start=2,
            frame_end=1,
            matte_provider=ObjectIndexMatteProvider(),
            settings=FeedbackSettings(),
            image_io=MemoryImageIO({}),
        )
    except ValueError as error:
        assert str(error) == "frame_start must not be greater than frame_end"
    else:
        raise AssertionError("processing accepted an inverted frame range")


def test_processing_session_exposes_its_initial_frame_without_processing_it(
    tmp_path: Path,
) -> None:
    paths = SequencePaths(tmp_path)
    frame = paths.frame(4)
    io = MemoryImageIO(
        {
            frame.beauty: _rgba(0.5),
            frame.vector: _rgba(0.0),
            frame.matte: np.ones((1, 2), dtype=np.float32),
        }
    )

    session = ProcessingSession.create(
        paths,
        frame_start=4,
        frame_end=4,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(),
        image_io=io,
    )

    assert session.current_frame == 4
    assert session.completed_frames == ()
    assert not session.is_finished
    assert io.written == {}


def test_processing_session_advances_one_frame_to_successful_completion(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    frame = paths.frame(4)
    io = MemoryImageIO(
        {
            frame.beauty: _rgba(0.5),
            frame.vector: _rgba(0.0),
            frame.matte: np.ones((1, 2), dtype=np.float32),
        }
    )
    session = ProcessingSession.create(
        paths,
        frame_start=4,
        frame_end=4,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(),
        image_io=io,
    )

    session.process_next_frame()

    assert session.is_finished
    assert session.completed_frames == (frame.processed,)
    assert session.result.frames == (frame.processed,)
    np.testing.assert_array_equal(io.written[frame.processed], _rgba(0.5))

    session.process_next_frame()
    assert session.completed_frames == (frame.processed,)


def test_processing_session_processes_at_most_one_frame_per_advancement(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    images: dict[Path, np.ndarray] = {}
    for frame_number in (1, 2):
        frame = paths.frame(frame_number)
        images[frame.beauty] = _rgba(float(frame_number))
        images[frame.vector] = _rgba(0.0)
        images[frame.matte] = np.ones((1, 2), dtype=np.float32)
    io = MemoryImageIO(images)
    session = ProcessingSession.create(
        paths,
        frame_start=1,
        frame_end=2,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(),
        image_io=io,
    )

    session.process_next_frame()

    assert session.current_frame == 2
    assert session.completed_frames == (paths.frame(1).processed,)
    assert not session.is_finished
    assert paths.frame(2).processed not in io.written


def test_processing_session_does_not_advance_after_a_frame_failure(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    frame = paths.frame(7)
    io = MemoryImageIO(
        {
            frame.beauty: _rgba(0.5),
            frame.matte: np.ones((1, 2), dtype=np.float32),
        }
    )
    session = ProcessingSession.create(
        paths,
        frame_start=7,
        frame_end=7,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(),
        image_io=io,
    )

    with pytest.raises(FileNotFoundError, match="Missing vector input for frame 7"):
        session.process_next_frame()
    io.images[frame.vector] = _rgba(0.0)
    session.process_next_frame()

    assert session.is_finished
    assert session.completed_frames == ()
    assert io.written == {}
    manifest = json.loads(sequence_manifest_path(paths).read_text(encoding="utf-8"))
    assert manifest["completed_frames"] == []
    with pytest.raises(RuntimeError, match="did not complete successfully"):
        _ = session.result


def test_processing_session_resumes_after_its_last_complete_frame(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    images: dict[Path, np.ndarray] = {}
    for frame_number, beauty_value in ((1, 0.75), (2, 0.0)):
        frame = paths.frame(frame_number)
        images[frame.beauty] = _rgba(beauty_value)
        images[frame.vector] = _rgba(0.0)
        images[frame.matte] = np.ones((1, 2), dtype=np.float32)
    io = MemoryImageIO(images)
    cancellation_requested = False
    first_session = ProcessingSession.create(
        paths,
        frame_start=1,
        frame_end=2,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(persistence=1.0, block_size=1),
        image_io=io,
        should_cancel=lambda: cancellation_requested,
    )
    first_session.process_next_frame()
    cancellation_requested = True
    with pytest.raises(SequenceProcessingCancelled):
        first_session.process_next_frame()

    resumed = ProcessingSession.create(
        paths,
        frame_start=1,
        frame_end=2,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(persistence=1.0, block_size=1),
        image_io=io,
        run_mode=SequenceRunMode.RESUME,
    )

    assert resumed.current_frame == 2
    resumed.process_next_frame()
    np.testing.assert_array_equal(io.written[paths.frame(2).processed], _rgba(0.75))


def test_process_sequence_reads_the_exact_discovered_raw_paths(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    expected = paths.frame(1)
    discovered = FramePaths(
        frame=1,
        beauty=tmp_path / "emitted" / "beauty-result.exr",
        vector=tmp_path / "emitted" / "vector-result.exr",
        matte=tmp_path / "emitted" / "matte-result.exr",
        processed=expected.processed,
    )
    io = MemoryImageIO(
        {
            discovered.beauty: _rgba(0.5),
            discovered.vector: _rgba(0.0),
            discovered.matte: np.ones((1, 2), dtype=np.float32),
        }
    )

    result = process_sequence(
        paths,
        frame_start=1,
        frame_end=1,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(),
        image_io=io,
        input_frames=(discovered,),
    )

    assert result.frames == (expected.processed,)
    np.testing.assert_array_equal(io.written[expected.processed], _rgba(0.5))


def test_process_sequence_initializes_then_carries_feedback_in_frame_order(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    first = paths.frame(1)
    second = paths.frame(2)
    matte = np.ones((1, 2), dtype=np.float32)
    io = MemoryImageIO(
        {
            first.beauty: _rgba(0.25),
            first.vector: _rgba(0.0),
            first.matte: matte,
            second.beauty: _rgba(0.0),
            second.vector: _rgba(0.0),
            second.matte: matte,
        }
    )

    result = process_sequence(
        paths,
        frame_start=1,
        frame_end=2,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(persistence=1.0, block_size=1),
        image_io=io,
    )

    assert result.frames == (first.processed, second.processed)
    np.testing.assert_array_equal(io.written[first.processed], _rgba(0.25))
    np.testing.assert_array_equal(io.written[second.processed], _rgba(0.25))


def test_process_sequence_applies_explicit_resets_and_always_resets_first_frame(
    tmp_path: Path,
) -> None:
    paths = SequencePaths(tmp_path)
    first = paths.frame(1)
    second = paths.frame(2)
    matte = np.ones((1, 2), dtype=np.float32)
    io = MemoryImageIO(
        {
            first.beauty: _rgba(0.75),
            first.vector: _rgba(0.0),
            first.matte: matte,
            second.beauty: _rgba(0.25),
            second.vector: _rgba(0.0),
            second.matte: matte,
        }
    )

    process_sequence(
        paths,
        frame_start=1,
        frame_end=2,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(persistence=1.0, block_size=1),
        image_io=io,
        reset_frames=parse_reset_frames("2"),
    )

    np.testing.assert_array_equal(io.written[first.processed], _rgba(0.75))
    np.testing.assert_array_equal(io.written[second.processed], _rgba(0.25))


def test_trail_sequence_carries_moving_mask_history_until_an_explicit_reset(
    tmp_path: Path,
) -> None:
    paths = SequencePaths(tmp_path)
    images: dict[Path, np.ndarray] = {}
    for frame_number, beauty_value, matte in (
        (1, 1.0, np.array([[1.0, 0.0]], dtype=np.float32)),
        (2, 0.0, np.array([[0.0, 1.0]], dtype=np.float32)),
        (3, 0.25, np.array([[0.0, 1.0]], dtype=np.float32)),
    ):
        frame = paths.frame(frame_number)
        images[frame.beauty] = _rgba(beauty_value)
        images[frame.vector] = _rgba(0.0)
        images[frame.matte] = matte
    io = MemoryImageIO(images)

    process_sequence(
        paths,
        frame_start=1,
        frame_end=3,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(
            mode=FeedbackMode.TRAIL,
            trail_decay=0.5,
            persistence=1.0,
            block_size=1,
        ),
        image_io=io,
        reset_frames=frozenset({3}),
    )

    np.testing.assert_allclose(io.written[paths.frame(2).processed][0, 0], np.full(4, 0.5))
    np.testing.assert_array_equal(io.written[paths.frame(3).processed], _rgba(0.25))


def test_resolution_change_resets_history_when_configured(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    first = paths.frame(1)
    second = paths.frame(2)
    io = MemoryImageIO(
        {
            first.beauty: _rgba(0.75),
            first.vector: _rgba(0.0),
            first.matte: np.ones((1, 2), dtype=np.float32),
            second.beauty: np.full((2, 2, 4), 0.25, dtype=np.float32),
            second.vector: np.zeros((2, 2, 4), dtype=np.float32),
            second.matte: np.ones((2, 2), dtype=np.float32),
        }
    )

    process_sequence(
        paths,
        frame_start=1,
        frame_end=2,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(persistence=1.0, block_size=1),
        image_io=io,
        resolution_change=ResolutionChangePolicy.RESET,
    )

    np.testing.assert_array_equal(io.written[second.processed], io.images[second.beauty])


def test_process_sequence_reports_the_missing_pass_and_frame(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    frame = paths.frame(7)
    io = MemoryImageIO(
        {
            frame.beauty: _rgba(0.0),
            frame.matte: np.ones((1, 2), dtype=np.float32),
        }
    )

    try:
        process_sequence(
            paths,
            frame_start=7,
            frame_end=7,
            matte_provider=ObjectIndexMatteProvider(),
            settings=FeedbackSettings(),
            image_io=io,
        )
    except FileNotFoundError as error:
        assert str(error) == f"Missing vector input for frame 7: {frame.vector}"
    else:
        raise AssertionError("processing accepted a frame with no vector input")

    assert io.written == {}


def test_process_sequence_always_ends_progress_after_an_unreadable_input(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    frame = paths.frame(3)
    progress = ProgressRecorder()

    class UnreadableImageIO(MemoryImageIO):
        def read_rgba(self, path: str | Path) -> np.ndarray:
            raise RuntimeError(f"Cannot decode image: {path}")

    try:
        process_sequence(
            paths,
            frame_start=3,
            frame_end=3,
            matte_provider=ObjectIndexMatteProvider(),
            settings=FeedbackSettings(),
            image_io=UnreadableImageIO({}),
            progress=progress,
        )
    except RuntimeError as error:
        assert str(error) == f"Cannot decode image: {frame.beauty}"
    else:
        raise AssertionError("processing accepted an unreadable beauty input")

    assert progress.events == [("begin", 1), ("end", 0)]


def test_process_sequence_honors_cancellation_between_complete_frames(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    first = paths.frame(1)
    second = paths.frame(2)
    matte = np.ones((1, 2), dtype=np.float32)
    io = MemoryImageIO(
        {
            first.beauty: _rgba(0.25),
            first.vector: _rgba(0.0),
            first.matte: matte,
            second.beauty: _rgba(0.0),
            second.vector: _rgba(0.0),
            second.matte: matte,
        }
    )
    progress = ProgressRecorder()

    try:
        process_sequence(
            paths,
            frame_start=1,
            frame_end=2,
            matte_provider=ObjectIndexMatteProvider(),
            settings=FeedbackSettings(),
            image_io=io,
            progress=progress,
            should_cancel=lambda: ("update", 1) in progress.events,
        )
    except SequenceProcessingCancelled as error:
        assert error.completed_frames == (first.processed,)
    else:
        raise AssertionError("processing ignored cancellation between frames")

    assert tuple(io.written) == (first.processed,)
    assert second.processed not in io.written
    assert progress.events == [("begin", 2), ("update", 1), ("end", 0)]


def test_cancelled_sequence_resumes_from_its_last_complete_frame(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    matte = np.ones((1, 2), dtype=np.float32)
    images: dict[Path, np.ndarray] = {}
    for frame_number, beauty_value in ((1, 0.75), (2, 0.0), (3, 0.0)):
        frame = paths.frame(frame_number)
        images[frame.beauty] = _rgba(beauty_value)
        images[frame.vector] = _rgba(0.0)
        images[frame.matte] = matte
    io = MemoryImageIO(images)
    progress = ProgressRecorder()

    try:
        process_sequence(
            paths,
            frame_start=1,
            frame_end=3,
            matte_provider=ObjectIndexMatteProvider(),
            settings=FeedbackSettings(persistence=1.0, block_size=1),
            image_io=io,
            progress=progress,
            should_cancel=lambda: ("update", 1) in progress.events,
        )
    except SequenceProcessingCancelled:
        pass
    else:
        raise AssertionError("processing ignored cancellation")

    io.written.clear()
    result = process_sequence(
        paths,
        frame_start=1,
        frame_end=3,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(persistence=1.0, block_size=1),
        image_io=io,
        run_mode=SequenceRunMode.RESUME,
    )

    assert result.frames == (paths.frame(2).processed, paths.frame(3).processed)
    assert paths.frame(1).processed not in io.written
    np.testing.assert_array_equal(io.written[paths.frame(3).processed], _rgba(0.75))


def test_trail_sequence_resume_restores_decayed_selected_object_coverage(
    tmp_path: Path,
) -> None:
    paths = SequencePaths(tmp_path)
    images: dict[Path, np.ndarray] = {}
    for frame_number, beauty_value, matte in (
        (1, 1.0, np.array([[1.0, 0.0]], dtype=np.float32)),
        (2, 0.0, np.array([[0.0, 1.0]], dtype=np.float32)),
        (3, 0.0, np.array([[0.0, 1.0]], dtype=np.float32)),
    ):
        frame = paths.frame(frame_number)
        images[frame.beauty] = _rgba(beauty_value)
        images[frame.vector] = _rgba(0.0)
        images[frame.matte] = matte
    io = MemoryImageIO(images)
    progress = ProgressRecorder()
    settings = FeedbackSettings(
        mode=FeedbackMode.TRAIL,
        trail_decay=0.5,
        persistence=1.0,
        block_size=1,
    )

    with pytest.raises(SequenceProcessingCancelled):
        process_sequence(
            paths,
            frame_start=1,
            frame_end=3,
            matte_provider=ObjectIndexMatteProvider(),
            settings=settings,
            image_io=io,
            progress=progress,
            should_cancel=lambda: ("update", 2) in progress.events,
        )

    io.written.clear()
    process_sequence(
        paths,
        frame_start=1,
        frame_end=3,
        matte_provider=ObjectIndexMatteProvider(),
        settings=settings,
        image_io=io,
        run_mode=SequenceRunMode.RESUME,
    )

    np.testing.assert_allclose(
        io.written[paths.frame(3).processed][0, 0],
        np.full(4, 0.125, dtype=np.float32),
    )


def test_trail_resume_rebuilds_only_one_history_frame_per_session_step(
    tmp_path: Path,
) -> None:
    paths = SequencePaths(tmp_path)
    matte = np.ones((1, 2), dtype=np.float32)
    images: dict[Path, np.ndarray] = {}
    for frame_number in (0, 1, 2):
        frame = paths.frame(frame_number)
        images[frame.beauty] = _rgba((frame_number + 1) / 4.0)
        images[frame.vector] = _rgba(0.0)
        images[frame.matte] = matte
    io = MemoryImageIO(images)
    settings = FeedbackSettings(mode=FeedbackMode.TRAIL, block_size=1)
    interrupted = ProcessingSession.create(
        paths,
        frame_start=0,
        frame_end=2,
        matte_provider=ObjectIndexMatteProvider(),
        settings=settings,
        image_io=io,
    )
    interrupted.process_next_frame()
    interrupted.process_next_frame()

    io.reads.clear()
    resumed = ProcessingSession.create(
        paths,
        frame_start=0,
        frame_end=2,
        matte_provider=ObjectIndexMatteProvider(),
        settings=settings,
        image_io=io,
        run_mode=SequenceRunMode.RESUME,
    )

    assert io.reads == []
    assert resumed.recovery_frame == 0
    resumed.process_next_frame()
    assert resumed.recovery_frame == 1
    assert paths.frame(2).processed not in io.written
    resumed.process_next_frame()
    assert resumed.recovery_frame is None
    assert not resumed.is_finished
    resumed.process_next_frame()
    assert resumed.is_finished
    assert resumed.result.frames == (paths.frame(2).processed,)


def test_complete_trail_resume_applies_missing_history_policy_at_the_failed_frame(
    tmp_path: Path,
) -> None:
    paths = SequencePaths(tmp_path)
    matte = np.ones((1, 2), dtype=np.float32)
    images: dict[Path, np.ndarray] = {}
    for frame_number in (1, 2, 3):
        frame = paths.frame(frame_number)
        images[frame.beauty] = _rgba(frame_number / 4.0)
        images[frame.vector] = _rgba(0.0)
        images[frame.matte] = matte
    io = MemoryImageIO(images)
    settings = FeedbackSettings(mode=FeedbackMode.TRAIL, block_size=1)
    process_sequence(
        paths,
        frame_start=1,
        frame_end=3,
        matte_provider=ObjectIndexMatteProvider(),
        settings=settings,
        image_io=io,
    )
    io.images[paths.frame(2).processed][0, 0, 0] = np.nan

    with pytest.raises(RuntimeError, match="invalid for frame 2"):
        process_sequence(
            paths,
            frame_start=1,
            frame_end=3,
            matte_provider=ObjectIndexMatteProvider(),
            settings=settings,
            image_io=io,
            run_mode=SequenceRunMode.RESUME,
        )

    io.written.clear()
    result = process_sequence(
        paths,
        frame_start=1,
        frame_end=3,
        matte_provider=ObjectIndexMatteProvider(),
        settings=settings,
        image_io=io,
        run_mode=SequenceRunMode.RESUME,
        missing_history=MissingHistoryPolicy.RESET,
    )

    assert result.frames == (paths.frame(2).processed, paths.frame(3).processed)
    assert json.loads(sequence_manifest_path(paths).read_text(encoding="utf-8"))[
        "completed_frames"
    ] == [1, 2, 3]


def test_resume_reprocesses_from_a_missing_history_frame_when_configured(
    tmp_path: Path,
) -> None:
    paths = SequencePaths(tmp_path)
    matte = np.ones((1, 2), dtype=np.float32)
    images: dict[Path, np.ndarray] = {}
    for frame_number in (1, 2):
        frame = paths.frame(frame_number)
        images[frame.beauty] = _rgba(float(frame_number) / 4.0)
        images[frame.vector] = _rgba(0.0)
        images[frame.matte] = matte
    io = MemoryImageIO(images)
    progress = ProgressRecorder()
    with pytest.raises(SequenceProcessingCancelled):
        process_sequence(
            paths,
            frame_start=1,
            frame_end=2,
            matte_provider=ObjectIndexMatteProvider(),
            settings=FeedbackSettings(),
            image_io=io,
            progress=progress,
            should_cancel=lambda: ("update", 1) in progress.events,
        )

    paths.frame(1).processed.unlink()
    io.images.pop(paths.frame(1).processed)
    io.written.clear()
    result = process_sequence(
        paths,
        frame_start=1,
        frame_end=2,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(),
        image_io=io,
        run_mode=SequenceRunMode.RESUME,
        missing_history=MissingHistoryPolicy.RESET,
    )

    assert result.frames == (paths.frame(1).processed, paths.frame(2).processed)


def test_resume_resets_when_blender_reports_unreadable_history(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    first = paths.frame(1)
    second = paths.frame(2)
    matte = np.ones((1, 2), dtype=np.float32)
    io = MemoryImageIO(
        {
            first.beauty: _rgba(0.75),
            first.vector: _rgba(0.0),
            first.matte: matte,
            second.beauty: _rgba(0.25),
            second.vector: _rgba(0.0),
            second.matte: matte,
        }
    )
    progress = ProgressRecorder()
    with pytest.raises(SequenceProcessingCancelled):
        process_sequence(
            paths,
            frame_start=1,
            frame_end=2,
            matte_provider=ObjectIndexMatteProvider(),
            settings=FeedbackSettings(persistence=1.0, block_size=1),
            image_io=io,
            progress=progress,
            should_cancel=lambda: ("update", 1) in progress.events,
        )

    class UnreadableHistoryImageIO(MemoryImageIO):
        def read_rgba(self, path: str | Path) -> np.ndarray:
            if Path(path) == first.processed:
                raise RuntimeError(f"Cannot decode image: {path}")
            return super().read_rgba(path)

    resume_io = UnreadableHistoryImageIO(io.images)
    result = process_sequence(
        paths,
        frame_start=1,
        frame_end=2,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(persistence=1.0, block_size=1),
        image_io=resume_io,
        run_mode=SequenceRunMode.RESUME,
        missing_history=MissingHistoryPolicy.RESET,
    )

    assert result.frames == (first.processed, second.processed)
    np.testing.assert_array_equal(resume_io.written[first.processed], _rgba(0.75))
    np.testing.assert_array_equal(resume_io.written[second.processed], _rgba(0.75))


def test_resume_rejects_outputs_from_incompatible_feedback_settings(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    frame = paths.frame(1)
    matte = np.ones((1, 2), dtype=np.float32)
    io = MemoryImageIO(
        {
            frame.beauty: _rgba(0.5),
            frame.vector: _rgba(0.0),
            frame.matte: matte,
        }
    )
    process_sequence(
        paths,
        frame_start=1,
        frame_end=1,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(persistence=0.5),
        image_io=io,
    )

    try:
        process_sequence(
            paths,
            frame_start=1,
            frame_end=1,
            matte_provider=ObjectIndexMatteProvider(),
            settings=FeedbackSettings(persistence=0.75),
            image_io=io,
            run_mode=SequenceRunMode.RESUME,
        )
    except ValueError as error:
        assert str(error) == (
            "Sequence recovery manifest is incompatible: settings_fingerprint changed"
        )
    else:
        raise AssertionError("processing resumed outputs made with incompatible settings")


def test_resume_rejects_discontinuous_completion_metadata(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    matte = np.ones((1, 2), dtype=np.float32)
    images: dict[Path, np.ndarray] = {}
    for frame_number in (1, 2, 3):
        frame = paths.frame(frame_number)
        images[frame.beauty] = _rgba(0.25)
        images[frame.vector] = _rgba(0.0)
        images[frame.matte] = matte
    io = MemoryImageIO(images)
    process_sequence(
        paths,
        frame_start=1,
        frame_end=3,
        matte_provider=ObjectIndexMatteProvider(),
        settings=FeedbackSettings(),
        image_io=io,
    )
    manifest_path = sequence_manifest_path(paths)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["completed_frames"] = [1, 3]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="Sequence recovery manifest has discontinuous completed frames",
    ):
        process_sequence(
            paths,
            frame_start=1,
            frame_end=3,
            matte_provider=ObjectIndexMatteProvider(),
            settings=FeedbackSettings(),
            image_io=io,
            run_mode=SequenceRunMode.RESUME,
        )


def test_process_sequence_refuses_existing_processed_output_by_default(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    frame = paths.frame(1)
    frame.processed.parent.mkdir(parents=True)
    frame.processed.write_bytes(b"existing")
    io = MemoryImageIO({})

    try:
        process_sequence(
            paths,
            frame_start=1,
            frame_end=1,
            matte_provider=ObjectIndexMatteProvider(),
            settings=FeedbackSettings(),
            image_io=io,
        )
    except FileExistsError as error:
        assert str(error) == f"Processed output exists and overwrite is disabled: {frame.processed}"
    else:
        raise AssertionError("processing overwrote an existing output without permission")

    assert frame.processed.read_bytes() == b"existing"
    assert io.written == {}


def test_process_sequence_rejects_mismatched_pass_dimensions(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    frame = paths.frame(1)
    io = MemoryImageIO(
        {
            frame.beauty: _rgba(0.0),
            frame.vector: np.zeros((2, 2, 4), dtype=np.float32),
            frame.matte: np.ones((1, 2), dtype=np.float32),
        }
    )

    try:
        process_sequence(
            paths,
            frame_start=1,
            frame_end=1,
            matte_provider=ObjectIndexMatteProvider(),
            settings=FeedbackSettings(),
            image_io=io,
        )
    except ValueError as error:
        assert str(error) == "motion must match beauty shape (height, width, 4)"
    else:
        raise AssertionError("processing accepted mismatched pass dimensions")

    assert io.written == {}
