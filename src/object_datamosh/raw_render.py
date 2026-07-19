"""Sequential and incremental raw-pass rendering through Blender's public render seam."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import bpy

from .core.paths import FramePaths, SequencePaths
from .raw_render_operation import RenderFrameRequest


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


OutputPathsContext = Callable[[Any, Any, SequencePaths], AbstractContextManager[None]]


def _temporary_output_paths(
    scene: Any, view_layer: Any, paths: SequencePaths
) -> AbstractContextManager[None]:
    # Keep Blender compositor integration lazy so the incremental session remains testable through
    # its injected output-path boundary outside Blender.
    from .compositor_setup import temporary_raw_output_paths

    return temporary_raw_output_paths(scene, view_layer, paths)


class RawRenderSession:
    """Prepare and verify one raw frame at a time while owning temporary scene changes."""

    def __init__(
        self,
        scene: Any,
        view_layer: Any,
        paths: SequencePaths,
        *,
        frame_start: int,
        frame_end: int,
        overwrite: bool,
        output_context: AbstractContextManager[None],
    ) -> None:
        self.scene = scene
        self.view_layer = view_layer
        self.paths = paths
        self.frame_start = frame_start
        self.frame_end = frame_end
        self.current_frame = frame_start
        self._overwrite = overwrite
        self.completed_frames: tuple[FramePaths, ...] = ()
        self._original_frame = scene.frame_current
        self._output_context = output_context
        self._pending_request: RenderFrameRequest | None = None
        self._before: tuple[dict[Path, tuple[int, int]], ...] | None = None
        self._closed = False

    @classmethod
    def create(
        cls,
        scene: Any,
        view_layer: Any,
        paths: SequencePaths,
        *,
        frame_start: int,
        frame_end: int,
        overwrite: bool = False,
        output_paths_context: OutputPathsContext = _temporary_output_paths,
    ) -> RawRenderSession:
        """Validate a run and acquire its temporary output-path configuration."""
        if scene.view_layers.get(view_layer.name) != view_layer:
            raise ValueError("View layer must belong to the rendered scene")
        if frame_start > frame_end:
            raise ValueError("frame_start must not be greater than frame_end")
        collisions = _collision_paths(paths, frame_start, frame_end)
        if collisions and not overwrite:
            preview = ", ".join(str(path) for path in collisions[:3])
            raise FileExistsError(f"Raw output exists and overwrite is disabled: {preview}")
        output_context = output_paths_context(scene, view_layer, paths)
        output_context.__enter__()
        return cls(
            scene,
            view_layer,
            paths,
            frame_start=frame_start,
            frame_end=frame_end,
            overwrite=overwrite,
            output_context=output_context,
        )

    @property
    def is_finished(self) -> bool:
        return self.current_frame > self.frame_end

    @property
    def result(self) -> RawRenderResult:
        if not self.is_finished:
            raise RuntimeError("Raw rendering is not complete")
        return RawRenderResult(self.completed_frames)

    def prepare_next_frame(self) -> RenderFrameRequest:
        """Snapshot pass directories and move the scene to the next frame."""
        if self._closed:
            raise RuntimeError("Raw render session is closed")
        if self._pending_request is not None:
            raise RuntimeError("A raw frame render is already active")
        if self.is_finished:
            raise RuntimeError("Raw rendering is already complete")
        expected = self.paths.frame(self.current_frame)
        if not self._overwrite:
            late_collisions = tuple(
                path
                for path in (expected.beauty, expected.vector, expected.matte)
                if path.exists()
            )
            if late_collisions:
                preview = ", ".join(str(path) for path in late_collisions)
                raise FileExistsError(
                    f"Raw output appeared after rendering started and overwrite is disabled: {preview}"
                )
        directories = (expected.beauty.parent, expected.vector.parent, expected.matte.parent)
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        self._before = tuple(_snapshot(directory) for directory in directories)
        self.scene.frame_set(self.current_frame)
        request = RenderFrameRequest(
            frame=self.current_frame,
            scene=self.scene,
            view_layer=self.view_layer,
        )
        self._pending_request = request
        return request

    def complete_frame(self, request: RenderFrameRequest) -> FramePaths:
        """Verify exactly one newly emitted file for every configured pass."""
        if request is not self._pending_request or self._before is None:
            raise RuntimeError("Render completion does not belong to the active raw frame")
        expected = self.paths.frame(request.frame)
        directories = (expected.beauty.parent, expected.vector.parent, expected.matte.parent)
        actual = FramePaths(
            frame=request.frame,
            beauty=_discover_output(directories[0], self._before[0], "beauty"),
            vector=_discover_output(directories[1], self._before[1], "vector"),
            matte=_discover_output(directories[2], self._before[2], "matte"),
            processed=expected.processed,
        )
        self.completed_frames += (actual,)
        self.current_frame += 1
        self._pending_request = None
        self._before = None
        logging.getLogger(__name__).info(
            "Rendered raw frame %d: beauty=%s, vector=%s, matte=%s",
            request.frame,
            actual.beauty,
            actual.vector,
            actual.matte,
        )
        return actual

    def close(self) -> None:
        """Restore the scene frame and temporary output paths exactly once."""
        if self._closed:
            return
        self._closed = True
        try:
            self.scene.frame_set(self._original_frame)
        finally:
            self._output_context.__exit__(None, None, None)


def render_raw_passes(
    scene: Any,
    view_layer: Any,
    paths: SequencePaths,
    *,
    frame_start: int,
    frame_end: int,
    overwrite: bool = False,
    progress: RenderProgress | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> RawRenderResult:
    """Synchronously drive the reusable incremental raw-render session."""
    session = RawRenderSession.create(
        scene,
        view_layer,
        paths,
        frame_start=frame_start,
        frame_end=frame_end,
        overwrite=overwrite,
    )
    progress_started = False
    try:
        if progress is not None:
            progress.begin(frame_end - frame_start + 1)
            progress_started = True
        while not session.is_finished:
            if should_cancel is not None and should_cancel():
                raise RawRenderCancelled(session.completed_frames)
            request = session.prepare_next_frame()
            bpy.ops.render.render(scene=scene.name, layer=view_layer.name)
            session.complete_frame(request)
            if progress is not None:
                progress.update(len(session.completed_frames))
        return session.result
    finally:
        try:
            session.close()
        finally:
            if progress is not None and progress_started:
                progress.end()
