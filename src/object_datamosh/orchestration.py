"""Render-and-process orchestration over the published phase services."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TypeVar

from .core.paths import FramePaths


class RenderedSequence(Protocol):
    """Result boundary supplied by the raw rendering service."""

    frames: tuple[FramePaths, ...]


class ProcessedSequence(Protocol):
    """Result boundary supplied by the sequence processing service."""

    frames: tuple[Path, ...]


class RenderAndProcessPhase(StrEnum):
    """Observable phases of a combined render-and-process run."""

    RENDERING = "RENDERING"
    PROCESSING = "PROCESSING"


_RenderedT = TypeVar("_RenderedT", bound=RenderedSequence)
_ProcessedT = TypeVar("_ProcessedT", bound=ProcessedSequence)


@dataclass(frozen=True, slots=True)
class RenderAndProcessResult:
    """Completed raw and processed outputs from a combined run."""

    raw: RenderedSequence
    processed: ProcessedSequence


def render_and_process(
    render: Callable[[], _RenderedT],
    process: Callable[[tuple[FramePaths, ...]], _ProcessedT],
    *,
    on_phase: Callable[[RenderAndProcessPhase], None] | None = None,
) -> RenderAndProcessResult:
    """Render a complete raw range, then process exactly the discovered frame paths.

    Exceptions and cancellation from either phase propagate unchanged. In particular, processing
    is never invoked unless rendering returns successfully.
    """
    if on_phase is not None:
        on_phase(RenderAndProcessPhase.RENDERING)
    raw = render()
    if on_phase is not None:
        on_phase(RenderAndProcessPhase.PROCESSING)
    processed = process(raw.frames)
    return RenderAndProcessResult(raw=raw, processed=processed)
