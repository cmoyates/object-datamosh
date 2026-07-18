"""Blender-backed scene-linear float image I/O.

``bpy`` is supplied by Blender 5.0 and has no compatible runtime package on PyPI, so its import is
narrowly ignored by the repository's external static checker.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import bpy
import numpy as np

from .core.contracts import FloatImage, FloatMask
from .core.ownership import OWNERSHIP_TAG, owned_name


class BlenderImageIO:
    """Read and write float RGBA images without retaining temporary Image data-blocks."""

    def read_rgba(self, path: str | Path) -> FloatImage:
        image_path = Path(path)
        if not image_path.is_file():
            raise FileNotFoundError(f"Image does not exist: {image_path}")
        _validate_exr_path(image_path)

        logging.getLogger(__name__).info("Reading RGBA OpenEXR image: %s", image_path)
        image = bpy.data.images.load(str(image_path), check_existing=False)
        try:
            image.name = owned_name(image.name)
            image[OWNERSHIP_TAG] = True
            width, height = image.size
            if not image.is_float:
                raise ValueError(f"Expected a floating-point OpenEXR image at {image_path}")
            if image.channels != 4:
                raise ValueError(
                    f"Expected an RGBA image at {image_path}, found {image.channels} channels"
                )
            logging.getLogger(__name__).debug(
                "Loaded %s: width=%d, height=%d, channels=%d, mapping=RGBA",
                image_path,
                width,
                height,
                image.channels,
            )
            pixels = np.empty(width * height * 4, dtype=np.float32)
            cast(Any, image.pixels).foreach_get(pixels)
            return pixels.reshape((height, width, 4))
        finally:
            bpy.data.images.remove(image)

    def read_mask(self, path: str | Path) -> FloatMask:
        """Read scalar matte coverage from the EXR red channel."""
        image_path = Path(path)
        logging.getLogger(__name__).info("Reading red-channel matte coverage: %s", image_path)
        return np.ascontiguousarray(self.read_rgba(image_path)[..., 0], dtype=np.float32)

    def write_rgba(self, path: str | Path, pixels: FloatImage) -> None:
        image_path = Path(path)
        _validate_exr_path(image_path)
        _validate_rgba(pixels)
        image_path.parent.mkdir(parents=True, exist_ok=True)

        height, width, channels = pixels.shape
        logging.getLogger(__name__).info(
            "Writing RGBA OpenEXR image: %s (width=%d, height=%d, channels=%d)",
            image_path,
            width,
            height,
            channels,
        )
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
            logging.getLogger(__name__).debug(
                "Saving %s as OPEN_EXR/RGBA/32 with ZIP codec", image_path
            )
            image.save_render(str(image_path), scene=scene)
        finally:
            (
                settings.file_format,
                settings.color_mode,
                settings.color_depth,
                settings.exr_codec,
            ) = original


def _validate_exr_path(path: Path) -> None:
    if path.suffix.lower() != ".exr":
        raise ValueError(f"BlenderImageIO requires an .exr path: {path}")


def _validate_rgba(pixels: FloatImage) -> None:
    if pixels.dtype != np.float32:
        raise TypeError("pixels must use float32")
    if pixels.ndim != 3 or pixels.shape[2] != 4:
        raise ValueError("pixels must have shape (height, width, 4)")
