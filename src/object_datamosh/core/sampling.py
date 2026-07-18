"""Coordinate sampling for Blender-independent image processing."""

import numpy as np
from numpy.typing import NDArray

from .contracts import FloatImage, FloatMask


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
    for name, array in (("image", image), ("sample_x", sample_x), ("sample_y", sample_y)):
        if not isinstance(array, np.ndarray):
            raise TypeError(f"{name} must be a NumPy array")
        if array.dtype != np.float32:
            raise TypeError(f"{name} must use float32")
    if image.ndim not in (2, 3):
        raise ValueError("image must be 2D or have a channel dimension")
    if image.shape[0] == 0 or image.shape[1] == 0:
        raise ValueError("image dimensions must be nonzero")
    if sample_x.shape != sample_y.shape:
        raise ValueError("sample_x and sample_y must have matching shapes")

    height, width = image.shape[:2]
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

    if image.ndim == 3:
        wx = wx[..., None]
        wy = wy[..., None]

    top = image[y0, x0] * (1.0 - wx) + image[y0, x1] * wx
    bottom = image[y1, x0] * (1.0 - wx) + image[y1, x1] * wx
    sampled = (top * (1.0 - wy) + bottom * wy).astype(np.float32, copy=False)
    if image.ndim == 3:
        sampled = np.where(valid[..., None], sampled, 0.0)
    else:
        sampled = np.where(valid, sampled, 0.0)
    return sampled.astype(np.float32, copy=False), valid
