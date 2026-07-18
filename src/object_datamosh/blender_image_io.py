"""Blender-backed scene-linear float image I/O.

``bpy`` is supplied by Blender 5.0 and has no compatible runtime package on PyPI, so its import is
narrowly ignored by the repository's external static checker.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import bpy
import numpy as np

from .core.contracts import FloatImage
from .core.ownership import OWNERSHIP_TAG, owned_name


class BlenderImageIO:
    """Read and write float RGBA images without retaining temporary Image data-blocks."""

    def read_rgba(self, path: str | Path) -> FloatImage:
        image_path = Path(path)
        if not image_path.is_file():
            raise FileNotFoundError(f"Image does not exist: {image_path}")

        image = bpy.data.images.load(str(image_path), check_existing=False)
        image[OWNERSHIP_TAG] = True
        try:
            width, height = image.size
            if image.channels != 4:
                raise ValueError(
                    f"Expected an RGBA image at {image_path}, found {image.channels} channels"
                )
            pixels = np.empty(width * height * 4, dtype=np.float32)
            cast(Any, image.pixels).foreach_get(pixels)
            return pixels.reshape((height, width, 4))
        finally:
            bpy.data.images.remove(image)

    def write_rgba(self, path: str | Path, pixels: FloatImage) -> None:
        image_path = Path(path)
        _validate_rgba(pixels)
        image_path.parent.mkdir(parents=True, exist_ok=True)

        height, width, _channels = pixels.shape
        image = bpy.data.images.new(
            owned_name(f"ImageIO_{uuid4().hex}"),
            width=width,
            height=height,
            alpha=True,
            float_buffer=True,
        )
        image[OWNERSHIP_TAG] = True
        try:
            cast(Any, image.colorspace_settings).name = "Linear Rec.709"
            cast(Any, image.pixels).foreach_set(np.ascontiguousarray(pixels).ravel())
            image.filepath_raw = str(image_path)
            image.file_format = "OPEN_EXR"
            self._save_full_float_exr(image, image_path)
        finally:
            bpy.data.images.remove(image)

    @staticmethod
    def _save_full_float_exr(image: bpy.types.Image, image_path: Path) -> None:
        scene = bpy.context.scene
        if scene is None:
            raise RuntimeError("A Blender scene is required to write an EXR image")
        settings = scene.render.image_settings
        original = (
            settings.file_format,
            settings.color_mode,
            settings.color_depth,
            settings.exr_codec,
        )
        try:
            settings.file_format = "OPEN_EXR"
            settings.color_mode = "RGBA"
            settings.color_depth = "32"
            settings.exr_codec = "ZIP"
            image.save_render(str(image_path), scene=scene)
        finally:
            (
                settings.file_format,
                settings.color_mode,
                settings.color_depth,
                settings.exr_codec,
            ) = original


def _validate_rgba(pixels: FloatImage) -> None:
    if pixels.dtype != np.float32:
        raise TypeError("pixels must use float32")
    if pixels.ndim != 3 or pixels.shape[2] != 4:
        raise ValueError("pixels must have shape (height, width, 4)")
