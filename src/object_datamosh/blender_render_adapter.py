"""Blender 5 render invocation and handler observation adapter."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import Any, Protocol, cast

import bpy

from .modal_lifecycle import RuntimeState
from .raw_render_operation import RenderEvent, RenderFrameRequest


class RenderHandlers(Protocol):
    """Narrow Blender application-handler surface used by raw rendering."""

    render_complete: list[Callable[..., None]]
    render_cancel: list[Callable[..., None]]


class BlenderRenderAdapter:
    """Launch one render and convert scene-owned Blender callbacks into polled state."""

    def __init__(
        self,
        runtime: RuntimeState,
        *,
        handlers: RenderHandlers | None = None,
        render_operator: Callable[..., set[str]] | None = None,
    ) -> None:
        self._runtime = runtime
        self._handlers = handlers if handlers is not None else bpy.app.handlers
        self._render_operator = render_operator or cast(
            Callable[..., set[str]], bpy.ops.render.render
        )
        self._event = RenderEvent.NONE
        self._error: Exception | None = None
        self._complete_handler: Callable[..., None] | None = None
        self._cancel_handler: Callable[..., None] | None = None

    @property
    def error(self) -> Exception | None:
        """Launch failure retained for phase-and-frame reporting by the controller."""
        return self._error

    def launch(self, request: RenderFrameRequest, run_identity: str) -> None:
        """Install narrowly scoped handlers, then invoke exactly one scene frame render."""
        if self._event is not RenderEvent.NONE or self._complete_handler is not None:
            raise RuntimeError("A Blender frame render is already being observed")
        expected_scene = request.scene

        def belongs_to_run(scene: object) -> bool:
            try:
                return scene is expected_scene and self._runtime.run_identity == run_identity
            except Exception:
                return False

        def completed(scene: object, _depsgraph: object | None = None) -> None:
            if self._event is RenderEvent.ACTIVE and belongs_to_run(scene):
                self._event = RenderEvent.COMPLETED

        def cancelled(scene: object, _depsgraph: object | None = None) -> None:
            if self._event is RenderEvent.ACTIVE and belongs_to_run(scene):
                self._event = RenderEvent.CANCELLED

        self._complete_handler = completed
        self._cancel_handler = cancelled
        self._handlers.render_complete.append(completed)
        self._handlers.render_cancel.append(cancelled)
        self._event = RenderEvent.ACTIVE
        self._error = None
        try:
            # Blender 5.0 cancels the owning modal operator after a nested INVOKE_DEFAULT render
            # completes. EXEC_DEFAULT is the reliable frame-boundary fallback: one frame may block,
            # then control returns to the parent modal lifecycle before another frame is launched.
            result = self._render_operator(
                "EXEC_DEFAULT",
                scene=cast(Any, request.scene).name,
                layer=cast(Any, request.view_layer).name,
            )
            if result is None:
                raise RuntimeError("Blender render invocation returned no status")
            if "CANCELLED" in result:
                self._event = RenderEvent.CANCELLED
            elif "FINISHED" in result:
                if self._event is RenderEvent.ACTIVE:
                    # Background mode executes synchronously. Blender normally emits
                    # render_complete before returning, but FINISHED is itself a safe boundary.
                    self._event = RenderEvent.COMPLETED
            elif "RUNNING_MODAL" not in result:
                raise RuntimeError(f"Unexpected Blender render result: {sorted(result)}")
            if self._event is not RenderEvent.ACTIVE:
                # EXEC_DEFAULT normally reaches its terminal boundary before returning. Stop
                # observing process-wide events immediately while retaining the state for poll().
                self._remove_handlers()
        except Exception as error:
            self._error = error
            self._event = RenderEvent.FAILED
            raise

    def poll(self) -> RenderEvent:
        """Return the latest callback-derived state without advancing Blender work."""
        return self._event

    def remove(self) -> None:
        """Remove only handlers owned by this adapter; safe to call repeatedly."""
        self._remove_handlers()
        self._event = RenderEvent.NONE

    def _remove_handlers(self) -> None:
        if self._complete_handler is not None:
            with suppress(ValueError):
                self._handlers.render_complete.remove(self._complete_handler)
            self._complete_handler = None
        if self._cancel_handler is not None:
            with suppress(ValueError):
                self._handlers.render_cancel.remove(self._cancel_handler)
            self._cancel_handler = None
