from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from object_datamosh.core.paths import SequencePaths
from object_datamosh.raw_render import RawRenderResult, RawRenderSession, render_raw_passes


class Scene:
    def __init__(self, view_layer: object) -> None:
        self.frame_current = 9
        self.frame_subframe = 0.625
        self.frames: list[int] = []
        self.view_layers = {"Main": view_layer}

    def frame_set(self, frame: int, *, subframe: float = 0.0) -> None:
        self.frame_current = frame
        self.frame_subframe = subframe
        self.frames.append(frame)


@contextmanager
def output_paths_context(*_args: object) -> Iterator[None]:
    yield


def test_session_discovers_emitted_paths_and_restores_the_scene_frame(tmp_path: Path) -> None:
    view_layer = SimpleNamespace(name="Main")
    scene = Scene(view_layer)
    paths = SequencePaths(tmp_path)
    session = RawRenderSession.create(
        scene,
        view_layer,
        paths,
        frame_start=3,
        frame_end=3,
        output_paths_context=output_paths_context,
    )

    request = session.prepare_next_frame()
    emitted = (
        tmp_path / "raw" / "beauty" / "actual-beauty-0003.exr",
        tmp_path / "raw" / "vector" / "actual-vector-0003.exr",
        tmp_path / "raw" / "matte" / "actual-matte-0003.exr",
    )
    for path in emitted:
        path.write_bytes(b"rendered")

    actual = session.complete_frame(request)
    session.close()

    assert (actual.beauty, actual.vector, actual.matte) == emitted
    assert session.result.frames == (actual,)
    assert scene.frames == [3, 9]
    assert scene.frame_subframe == 0.625


def test_output_paths_are_owned_only_during_an_active_frame(tmp_path: Path) -> None:
    events: list[str] = []

    @contextmanager
    def recording_context(*_args: object) -> Iterator[None]:
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    view_layer = SimpleNamespace(name="Main")
    scene = Scene(view_layer)
    paths = SequencePaths(tmp_path)
    session = RawRenderSession.create(
        scene,
        view_layer,
        paths,
        frame_start=1,
        frame_end=1,
        output_paths_context=recording_context,
    )
    assert events == []
    request = session.prepare_next_frame()
    assert events == ["enter"]
    expected = paths.frame(1)
    for path in (expected.beauty, expected.vector, expected.matte):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"rendered")
    session.complete_frame(request)
    assert events == ["enter", "exit"]
    session.close()


def test_session_close_aggregates_frame_and_output_restoration_failures(
    tmp_path: Path,
) -> None:
    class FailingScene(Scene):
        def frame_set(self, frame: int, *, subframe: float = 0.0) -> None:
            raise RuntimeError(f"cannot set {frame}:{subframe}")

    class FailingOutputContext:
        def __enter__(self) -> None:
            pass

        def __exit__(self, *_args: object) -> None:
            raise RuntimeError("nodes unavailable")

    view_layer = SimpleNamespace(name="Main")
    session = RawRenderSession.create(
        FailingScene(view_layer),
        view_layer,
        SequencePaths(tmp_path),
        frame_start=1,
        frame_end=1,
        output_paths_context=lambda *_args: FailingOutputContext(),
    )
    session._output_context = FailingOutputContext()

    with pytest.raises(RuntimeError) as raised:
        session.close()

    assert "temporary output-path restoration failed: nodes unavailable" in str(raised.value)
    assert "scene frame restoration to 9 (subframe 0.625) failed" in str(raised.value)


def test_session_constructor_failure_does_not_acquire_output_paths(tmp_path: Path) -> None:
    view_layer = SimpleNamespace(name="Main")

    class InvalidScene:
        view_layers = {"Main": view_layer}

        @property
        def frame_current(self) -> int:
            raise RuntimeError("scene was removed")

    class OutputContext:
        entered = False

        def __enter__(self) -> None:
            self.entered = True

        def __exit__(self, *_args: object) -> None:
            pass

    output_context = OutputContext()

    with pytest.raises(RuntimeError, match="scene was removed"):
        RawRenderSession.create(
            InvalidScene(),
            view_layer,
            SequencePaths(tmp_path),
            frame_start=1,
            frame_end=1,
            output_paths_context=lambda *_args: output_context,
        )

    assert not output_context.entered


def test_session_rejects_output_created_after_initial_collision_check(tmp_path: Path) -> None:
    view_layer = SimpleNamespace(name="Main")
    scene = Scene(view_layer)
    paths = SequencePaths(tmp_path)
    session = RawRenderSession.create(
        scene,
        view_layer,
        paths,
        frame_start=3,
        frame_end=3,
        overwrite=False,
        output_paths_context=output_paths_context,
    )
    late_output = paths.frame(3).beauty
    late_output.parent.mkdir(parents=True, exist_ok=True)
    late_output.write_bytes(b"external")

    with pytest.raises(FileExistsError, match="appeared after rendering started"):
        session.prepare_next_frame()

    assert late_output.read_bytes() == b"external"
    session.close()


def test_synchronous_progress_ends_when_session_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingCloseSession:
        is_finished = True
        result = RawRenderResult(())

        def close(self) -> None:
            raise RuntimeError("restore failed")

    class Progress:
        def __init__(self) -> None:
            self.events: list[object] = []

        def begin(self, total: int) -> None:
            self.events.append(("begin", total))

        def update(self, completed: int) -> None:
            self.events.append(("update", completed))

        def end(self) -> None:
            self.events.append("end")

    monkeypatch.setattr(
        RawRenderSession,
        "create",
        lambda *_args, **_kwargs: FailingCloseSession(),
    )
    progress = Progress()

    with pytest.raises(RuntimeError, match="restore failed"):
        render_raw_passes(
            object(),
            object(),
            SequencePaths(Path("unused")),
            frame_start=1,
            frame_end=1,
            progress=progress,
        )

    assert progress.events == [("begin", 1), "end"]
