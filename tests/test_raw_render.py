from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from object_datamosh.core.paths import SequencePaths
from object_datamosh.raw_render import RawRenderSession


class Scene:
    def __init__(self, view_layer: object) -> None:
        self.frame_current = 9
        self.frames: list[int] = []
        self.view_layers = {"Main": view_layer}

    def frame_set(self, frame: int) -> None:
        self.frame_current = frame
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
