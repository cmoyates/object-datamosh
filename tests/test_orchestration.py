from dataclasses import dataclass
from pathlib import Path

from object_datamosh.core.paths import FramePaths
from object_datamosh.orchestration import (
    RenderAndProcessPhase,
    render_and_process,
)


@dataclass(frozen=True)
class RenderResult:
    frames: tuple[FramePaths, ...]


@dataclass(frozen=True)
class ProcessResult:
    frames: tuple[Path, ...]


def test_render_and_process_hands_discovered_frames_to_processing_in_phase_order(
    tmp_path: Path,
) -> None:
    discovered = (
        FramePaths(
            frame=1,
            beauty=tmp_path / "actual-beauty.exr",
            vector=tmp_path / "actual-vector.exr",
            matte=tmp_path / "actual-matte.exr",
            processed=tmp_path / "processed.exr",
        ),
    )
    phases: list[RenderAndProcessPhase] = []
    received: list[tuple[FramePaths, ...]] = []

    def render() -> RenderResult:
        return RenderResult(discovered)

    def process(frames: tuple[FramePaths, ...]) -> ProcessResult:
        received.append(frames)
        return ProcessResult((frames[0].processed,))

    result = render_and_process(render, process, on_phase=phases.append)

    assert received == [discovered]
    assert phases == [RenderAndProcessPhase.RENDERING, RenderAndProcessPhase.PROCESSING]
    assert result.raw.frames == discovered
    assert result.processed.frames == (discovered[0].processed,)


def test_render_failure_never_starts_processing() -> None:
    phases: list[RenderAndProcessPhase] = []
    processing_started = False

    def render() -> RenderResult:
        raise RuntimeError("render failed")

    def process(frames: tuple[FramePaths, ...]) -> ProcessResult:
        nonlocal processing_started
        del frames
        processing_started = True
        return ProcessResult(())

    try:
        render_and_process(render, process, on_phase=phases.append)
    except RuntimeError as error:
        assert str(error) == "render failed"
    else:
        raise AssertionError("render failure did not propagate")

    assert not processing_started
    assert phases == [RenderAndProcessPhase.RENDERING]


def test_render_cancellation_never_starts_processing() -> None:
    class RenderCancelled(RuntimeError):
        pass

    processing_started = False

    def render() -> RenderResult:
        raise RenderCancelled("render cancelled")

    def process(frames: tuple[FramePaths, ...]) -> ProcessResult:
        nonlocal processing_started
        del frames
        processing_started = True
        return ProcessResult(())

    try:
        render_and_process(render, process)
    except RenderCancelled as error:
        assert str(error) == "render cancelled"
    else:
        raise AssertionError("render cancellation did not propagate")

    assert not processing_started
