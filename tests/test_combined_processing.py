from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from object_datamosh.combined_processing import (
    CombinedProcessingConfiguration,
    CombinedProcessingFailure,
)
from object_datamosh.core.contracts import FeedbackSettings
from object_datamosh.core.paths import FramePaths, SequencePaths
from object_datamosh.sequence_processing import ProcessingSession, ResolutionChangePolicy


class CompletedSession:
    current_frame = 3
    completed_frames: tuple[Path, ...] = ()
    is_finished = False

    def process_next_frame(self) -> None:
        self.completed_frames = (Path("processed-3.exr"),)
        self.is_finished = True

    @property
    def result(self):
        return SimpleNamespace(frames=self.completed_frames)


class FailingUpdateProgress:
    def begin(self, total: int) -> None:
        pass

    def update(self, completed: int) -> None:
        raise RuntimeError("progress display unavailable")

    def end(self) -> None:
        pass


def test_background_progress_failure_preserves_affected_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = CompletedSession()
    monkeypatch.setattr(ProcessingSession, "create", lambda *args, **kwargs: session)
    configuration = CombinedProcessingConfiguration(
        paths=SequencePaths(tmp_path),
        frame_start=3,
        frame_end=3,
        matte_provider=cast(Any, object()),
        feedback_settings=FeedbackSettings(),
        image_io=cast(Any, object()),
        overwrite=False,
        reset_frames=frozenset(),
        resolution_change=ResolutionChangePolicy.ERROR,
    )
    input_frames = (
        FramePaths(
            3,
            tmp_path / "beauty.exr",
            tmp_path / "vector.exr",
            tmp_path / "matte.exr",
            tmp_path / "processed.exr",
        ),
    )

    with pytest.raises(CombinedProcessingFailure) as failure:
        configuration.process(input_frames, FailingUpdateProgress())

    assert failure.value.frame == 3
    assert str(failure.value) == "progress display unavailable"
