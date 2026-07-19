"""Sequential and incremental raw-pass rendering through Blender's public render seam."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

import bpy

from .core.paths import FramePaths, SequencePaths


class RenderScene(Protocol):
    """Narrow scene identity required by the Blender render adapter."""

    name: str


class RenderViewLayer(Protocol):
    """Narrow view-layer identity required by raw rendering."""

    name: str


@dataclass(frozen=True, slots=True)
class RenderFrameRequest:
    """One scene-owned frame render passed through the Blender adapter boundary."""

    frame: int
    scene: RenderScene
    view_layer: RenderViewLayer


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


def _publish_output(staged: Path, destination: Path, *, overwrite: bool) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if overwrite:
        replacement = destination.with_name(f"ODM_publish_{uuid4().hex}_{destination.name}")
        os.link(staged, replacement)
        os.replace(replacement, destination)
    else:
        # A hard link publishes without a check/write race and never replaces user data. Retain
        # the owned staging link because output deletion requires a separate explicit action.
        os.link(staged, destination)
    return destination


def _publish_frame_outputs(
    discovered: tuple[Path, Path, Path],
    expected: FramePaths,
    *,
    overwrite: bool,
) -> None:
    destinations = (expected.beauty, expected.vector, expected.matte)
    try:
        for staged, destination in zip(discovered, destinations, strict=True):
            _publish_output(staged, destination, overwrite=overwrite)
    except Exception as error:
        raise RuntimeError(
            f"Canonical publication failed: {error}. The complete staged frame remains at "
            f"{discovered[0].parents[2]}; rerun with overwrite enabled to recover any "
            "canonical links published before the collision."
        ) from error


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
        output_paths_context: OutputPathsContext,
    ) -> None:
        self.scene = scene
        self.view_layer = view_layer
        self.paths = paths
        self._staging_paths = SequencePaths(
            paths.root / f"ODM_staging_{uuid4().hex}",
            frame_padding=paths.frame_padding,
        )
        self.frame_start = frame_start
        self.frame_end = frame_end
        self.current_frame = frame_start
        self._overwrite = overwrite
        self.completed_frames: tuple[FramePaths, ...] = ()
        self._original_frame = scene.frame_current
        self._original_subframe = getattr(scene, "frame_subframe", 0.0)
        self._output_paths_context = output_paths_context
        self._output_context: AbstractContextManager[None] | None = None
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
        return cls(
            scene,
            view_layer,
            paths,
            frame_start=frame_start,
            frame_end=frame_end,
            overwrite=overwrite,
            output_paths_context=output_paths_context,
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
        staged = self._staging_paths.frame(self.current_frame)
        if not self._overwrite:
            late_collisions = tuple(
                path for path in (expected.beauty, expected.vector, expected.matte) if path.exists()
            )
            if late_collisions:
                preview = ", ".join(str(path) for path in late_collisions)
                raise FileExistsError(
                    "Raw output appeared after rendering started and overwrite is disabled: "
                    f"{preview}"
                )
        directories = (staged.beauty.parent, staged.vector.parent, staged.matte.parent)
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        self._before = tuple(_snapshot(directory) for directory in directories)
        output_context = self._output_paths_context(
            self.scene, self.view_layer, self._staging_paths
        )
        output_context.__enter__()
        self._output_context = output_context
        try:
            self.scene.frame_set(self.current_frame)
        except Exception as error:
            try:
                self._release_output_context()
            except Exception as cleanup_error:
                raise RuntimeError(
                    f"Frame preparation failed: {error}; output-path cleanup failed: "
                    f"{cleanup_error}"
                ) from error
            raise
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
        staged = self._staging_paths.frame(request.frame)
        directories = (staged.beauty.parent, staged.vector.parent, staged.matte.parent)
        try:
            discovered = (
                _discover_output(directories[0], self._before[0], "beauty"),
                _discover_output(directories[1], self._before[1], "vector"),
                _discover_output(directories[2], self._before[2], "matte"),
            )
            _publish_frame_outputs(discovered, expected, overwrite=self._overwrite)
            actual = FramePaths(
                frame=request.frame,
                beauty=discovered[0],
                vector=discovered[1],
                matte=discovered[2],
                processed=expected.processed,
            )
        except Exception as error:
            try:
                self._release_output_context()
            except Exception as cleanup_error:
                raise RuntimeError(
                    f"Output verification failed: {error}; output-path cleanup failed: "
                    f"{cleanup_error}"
                ) from error
            raise
        self._release_output_context()
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
        cleanup_errors: list[str] = []
        try:
            self._release_output_context()
        except Exception as error:
            cleanup_errors.append(f"temporary output-path restoration failed: {error}")
        try:
            self.scene.frame_set(self._original_frame, subframe=self._original_subframe)
        except Exception as error:
            cleanup_errors.append(
                f"scene frame restoration to {self._original_frame} "
                f"(subframe {self._original_subframe}) failed: {error}"
            )
        if cleanup_errors:
            raise RuntimeError("; ".join(cleanup_errors))

    def _release_output_context(self) -> None:
        output_context, self._output_context = self._output_context, None
        if output_context is not None:
            output_context.__exit__(None, None, None)


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
            affected_frame = session.current_frame
            try:
                request = session.prepare_next_frame()
                render_result = cast(
                    set[str],
                    bpy.ops.render.render(scene=scene.name, layer=view_layer.name),
                )
                if "CANCELLED" in render_result:
                    raise RawRenderCancelled(session.completed_frames)
                if "FINISHED" not in render_result:
                    raise RuntimeError(f"Unexpected Blender render result: {sorted(render_result)}")
                session.complete_frame(request)
            except RawRenderCancelled:
                raise
            except Exception as error:
                raise RuntimeError(
                    f"Raw rendering failed at frame {affected_frame}: {error}"
                ) from error
            if progress is not None:
                progress.update(len(session.completed_frames))
        return session.result
    finally:
        try:
            session.close()
        finally:
            if progress is not None and progress_started:
                progress.end()
