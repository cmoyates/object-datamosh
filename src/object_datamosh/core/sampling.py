"""Coordinate sampling for Blender-independent image processing."""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from .contracts import FloatImage, FloatMask


@dataclass(frozen=True, slots=True)
class BilinearPlan:
    """Reusable coordinate work for same-sized 2D and channel images."""

    height: int
    width: int
    valid: NDArray[np.bool_]
    x0: NDArray[np.intp]
    x1: NDArray[np.intp]
    y0: NDArray[np.intp]
    y1: NDArray[np.intp]
    wx: NDArray[np.float32]
    wy: NDArray[np.float32]


def make_bilinear_plan(
    sample_x: NDArray[np.float32],
    sample_y: NDArray[np.float32],
    width: int,
    height: int,
) -> BilinearPlan:
    """Prepare pixel-space coordinates for repeated sampling of same-sized images."""
    for name, array in (("sample_x", sample_x), ("sample_y", sample_y)):
        if not isinstance(array, np.ndarray):
            raise TypeError(f"{name} must be a NumPy array")
        if array.dtype != np.float32:
            raise TypeError(f"{name} must use float32")
    if isinstance(width, bool) or not isinstance(width, int):
        raise TypeError("width must be an integer")
    if isinstance(height, bool) or not isinstance(height, int):
        raise TypeError("height must be an integer")
    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be nonzero")
    if sample_x.shape != sample_y.shape:
        raise ValueError("sample_x and sample_y must have matching shapes")

    finite = np.isfinite(sample_x) & np.isfinite(sample_y)
    valid = (
        finite
        & (sample_x >= 0.0)
        & (sample_x <= width - 1)
        & (sample_y >= 0.0)
        & (sample_y <= height - 1)
    )
    safe_x = np.where(finite, sample_x, 0.0).clip(0.0, width - 1)
    safe_y = np.where(finite, sample_y, 0.0).clip(0.0, height - 1)
    x0 = np.floor(safe_x).astype(np.intp)
    y0 = np.floor(safe_y).astype(np.intp)
    x1 = np.minimum(x0 + 1, width - 1)
    y1 = np.minimum(y0 + 1, height - 1)
    wx = (safe_x - x0).astype(np.float32)
    wy = (safe_y - y0).astype(np.float32)
    return BilinearPlan(height, width, valid, x0, x1, y0, y1, wx, wy)


def sample_with_plan(
    image: FloatImage | FloatMask,
    plan: BilinearPlan,
) -> tuple[FloatImage | FloatMask, NDArray[np.bool_]]:
    """Sample an image using coordinate work prepared for its dimensions."""
    if not isinstance(image, np.ndarray):
        raise TypeError("image must be a NumPy array")
    if image.dtype != np.float32:
        raise TypeError("image must use float32")
    if not isinstance(plan, BilinearPlan):
        raise TypeError("plan must be a BilinearPlan")
    if image.ndim not in (2, 3):
        raise ValueError("image must be 2D or have a channel dimension")
    if image.shape[0] == 0 or image.shape[1] == 0:
        raise ValueError("image dimensions must be nonzero")
    if image.shape[:2] != (plan.height, plan.width):
        raise ValueError("image dimensions must match the bilinear plan")

    wx = plan.wx[..., None] if image.ndim == 3 else plan.wx
    wy = plan.wy[..., None] if image.ndim == 3 else plan.wy
    top = image[plan.y0, plan.x0] * (1.0 - wx) + image[plan.y0, plan.x1] * wx
    bottom = image[plan.y1, plan.x0] * (1.0 - wx) + image[plan.y1, plan.x1] * wx
    sampled = (top * (1.0 - wy) + bottom * wy).astype(np.float32, copy=False)
    if image.ndim == 3:
        sampled = np.where(plan.valid[..., None], sampled, 0.0)
    else:
        sampled = np.where(plan.valid, sampled, 0.0)
    return sampled.astype(np.float32, copy=False), plan.valid


def bilinear_sample(
    image: FloatImage | FloatMask,
    sample_x: NDArray[np.float32],
    sample_y: NDArray[np.float32],
) -> tuple[FloatImage | FloatMask, NDArray[np.bool_]]:
    """Sample a 2D or channel image at pixel-space coordinates.

    Coordinates use ``x`` for columns and ``y`` for rows. The returned validity mask is true only
    where the complete sample lies inside the image; invalid samples are zero and never wrap to an
    opposite edge.
    """
    if not isinstance(image, np.ndarray):
        raise TypeError("image must be a NumPy array")
    if image.dtype != np.float32:
        raise TypeError("image must use float32")
    if image.ndim not in (2, 3):
        raise ValueError("image must be 2D or have a channel dimension")
    if image.shape[0] == 0 or image.shape[1] == 0:
        raise ValueError("image dimensions must be nonzero")
    height, width = image.shape[:2]
    plan = make_bilinear_plan(sample_x, sample_y, width, height)
    return sample_with_plan(image, plan)
