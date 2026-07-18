"""Sequential raw-pass rendering through Blender's public render seam."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import bpy
from bpy.types import Scene, ViewLayer

from .compositor_setup import temporary_raw_output_paths
from .core.paths import FramePaths, SequencePaths


class RenderProgress(Protocol):
    """Progress boundary used by raw rendering and Blender operators."""

    def begin(self, total: int) -> None: ...

    def update(self, completed: int) -> None: ...

    def end(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RawRenderResult:
    """Raw pass files discovered for a completed frame range."""

    frames: tuple[FramePaths, ...]


class RawRenderCancelled(RuntimeError):
    """Raised at a frame boundary after a caller requests cancellation."""

    def __init__(self, completed_frames: tuple[FramePaths, ...]) -> None:
        super().__init__(f"Raw rendering cancelled after {len(completed_frames)} frame(s)")
        self.completed_frames = completed_frames


def _signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def _snapshot(directory: Path) -> dict[Path, tuple[int, int]]:
    if not directory.is_dir():
        return {}
    return {path: _signature(path) for path in directory.glob("*.exr") if path.is_file()}


def _discover_output(directory: Path, before: dict[Path, tuple[int, int]], pass_name: str) -> Path:
    after = _snapshot(directory)
    changed = sorted(path for path, signature in after.items() if before.get(path) != signature)
    if len(changed) != 1:
        rendered = ", ".join(path.name for path in changed) or "none"
        raise RuntimeError(
            f"Expected one newly emitted {pass_name} EXR in {directory}, found: {rendered}"
        )
    return changed[0]


def _collision_paths(paths: SequencePaths, frame_start: int, frame_end: int) -> tuple[Path, ...]:
    collisions: list[Path] = []
    for frame in range(frame_start, frame_end + 1):
        frame_paths = paths.frame(frame)
        collisions.extend(
            path
            for path in (frame_paths.beauty, frame_paths.vector, frame_paths.matte)
            if path.exists()
        )
    return tuple(collisions)


def render_raw_passes(
    scene: Scene,
    view_layer: ViewLayer,
    paths: SequencePaths,
    *,
    frame_start: int,
    frame_end: int,
    overwrite: bool = False,
    progress: RenderProgress | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> RawRenderResult:
    """Render configured passes frame-by-frame and return files Blender actually emitted.

    Output-node paths and the scene's current frame are restored on every exit. Cancellation is
    observed before each frame, so a completed frame is always a recoverable boundary.
    """
    if scene.view_layers.get(view_layer.name) != view_layer:
        raise ValueError("View layer must belong to the rendered scene")
    if frame_start > frame_end:
        raise ValueError("frame_start must not be greater than frame_end")
    collisions = _collision_paths(paths, frame_start, frame_end)
    if collisions and not overwrite:
        preview = ", ".join(str(path) for path in collisions[:3])
        raise FileExistsError(f"Raw output exists and overwrite is disabled: {preview}")

    total = frame_end - frame_start + 1
    original_frame = scene.frame_current
    rendered: list[FramePaths] = []
    progress_started = False
    try:
        with temporary_raw_output_paths(scene, paths):
            if progress is not None:
                progress.begin(total)
                progress_started = True
            for frame in range(frame_start, frame_end + 1):
                if should_cancel is not None and should_cancel():
                    raise RawRenderCancelled(tuple(rendered))
                expected = paths.frame(frame)
                directories = (
                    expected.beauty.parent,
                    expected.vector.parent,
                    expected.matte.parent,
                )
                for directory in directories:
                    directory.mkdir(parents=True, exist_ok=True)
                before = tuple(_snapshot(directory) for directory in directories)

                scene.frame_set(frame)
                bpy.ops.render.render(scene=scene.name, layer=view_layer.name)
                actual = FramePaths(
                    frame=frame,
                    beauty=_discover_output(directories[0], before[0], "beauty"),
                    vector=_discover_output(directories[1], before[1], "vector"),
                    matte=_discover_output(directories[2], before[2], "matte"),
                    processed=expected.processed,
                )
                rendered.append(actual)
                logging.getLogger(__name__).info(
                    "Rendered raw frame %d: beauty=%s, vector=%s, matte=%s",
                    frame,
                    actual.beauty,
                    actual.vector,
                    actual.matte,
                )
                if progress is not None:
                    progress.update(len(rendered))
    finally:
        scene.frame_set(original_frame)
        if progress is not None and progress_started:
            progress.end()
    return RawRenderResult(frames=tuple(rendered))
