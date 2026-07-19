"""Frame-attributed failure boundary for synchronous combined workflow phases."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from .raw_render import RawRenderCancelled

_ResultT = TypeVar("_ResultT")


class CombinedRenderingFailure(RuntimeError):
    """Raw-render failure with the exact active frame preserved for its driver."""

    def __init__(self, frame: int, error: Exception) -> None:
        super().__init__(str(error))
        self.frame = frame


def render_with_frame_context(
    render: Callable[[], _ResultT],
    *,
    frame_start: int,
    frame_end: int,
    completed_count: Callable[[], int],
) -> _ResultT:
    """Run a synchronous raw phase and attribute infrastructure failures to its frame."""
    try:
        return render()
    except RawRenderCancelled:
        raise
    except Exception as error:
        active_frame = min(frame_start + completed_count(), frame_end)
        raise CombinedRenderingFailure(active_frame, error) from error
