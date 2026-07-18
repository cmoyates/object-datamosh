"""Image-sequence I/O boundary used by Blender-facing processing services."""

from pathlib import Path
from typing import Protocol

from .contracts import FloatImage, FloatMask


class ImageSequenceIO(Protocol):
    """Read and write scene-linear float32 RGBA images."""

    def read_rgba(self, path: str | Path) -> FloatImage:
        """Read ``path`` as ``(height, width, 4)`` float32 RGBA."""
        ...

    def read_mask(self, path: str | Path) -> FloatMask:
        """Read the red channel of ``path`` as ``(height, width)`` float32 coverage."""
        ...

    def write_rgba(self, path: str | Path, pixels: FloatImage) -> None:
        """Write scene-linear float32 RGBA pixels to ``path``."""
        ...
