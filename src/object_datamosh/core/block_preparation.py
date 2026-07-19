"""Prepare compact deterministic motion and refresh block grids."""

from dataclasses import dataclass
from numbers import Integral

import numpy as np
from numpy.typing import NDArray

from .contracts import FeedbackSettings, FloatImage, FloatMask, MotionChannels


@dataclass(frozen=True, slots=True)
class PreparedBlocks:
    """Compact per-block inputs for feedback pixel processing.

    ``displacement`` has shape ``(block_rows, block_columns, 2)`` and ``refresh``
    has shape ``(block_rows, block_columns)``. Partial blocks at the right and
    bottom edges occupy one grid cell just like full blocks.
    """

    displacement: FloatImage
    refresh: NDArray[np.bool_]
    block_size: int
    frame_shape: tuple[int, int]


def prepare_blocks(
    motion: FloatImage,
    matte: FloatMask,
    frame_number: int,
    settings: FeedbackSettings,
) -> PreparedBlocks:
    """Decode motion and return compact block displacement and refresh grids."""
    if isinstance(frame_number, bool) or not isinstance(frame_number, Integral):
        raise TypeError("frame_number must be an integer")
    if not isinstance(settings, FeedbackSettings):
        raise TypeError("settings must be a FeedbackSettings value")
    _validate_arrays(motion, matte)

    height, width = matte.shape
    effective_block_size = min(settings.block_size, max(height, width))
    channels = (0, 1) if settings.motion_channels is MotionChannels.RG else (2, 3)
    decoded_motion = motion[..., channels].astype(np.float64)
    if settings.reverse_motion:
        decoded_motion *= -1.0
    if settings.flip_x:
        decoded_motion[..., 0] *= -1.0
    if settings.flip_y:
        decoded_motion[..., 1] *= -1.0

    lengths = np.linalg.norm(decoded_motion, axis=-1)
    representable_clamp = min(settings.motion_clamp, np.finfo(np.float32).max)
    clamp_scales = np.divide(
        representable_clamp,
        lengths,
        out=np.full_like(lengths, np.inf),
        where=lengths > 0.0,
    )
    scales = np.minimum(settings.motion_gain, clamp_scales)
    displacement = (decoded_motion * scales[..., None]).astype(np.float32)
    representatives = _block_reduce(displacement, matte, effective_block_size)
    smallest_float32 = float(np.nextafter(np.float32(0.0), np.float32(1.0)))
    if settings.motion_quantization >= smallest_float32:
        step = settings.motion_quantization
        quantized = np.rint(representatives.astype(np.float64) / step) * step
        float32_limit = np.finfo(np.float32).max
        representatives = np.clip(quantized, -float32_limit, float32_limit).astype(np.float32)
    block_rows, block_columns = representatives.shape[:2]
    if settings.diffusion > 0.0:
        offsets = np.empty((*representatives.shape[:2], 2), dtype=np.float64)
        for component in range(2):
            random_values = _unit_random_grid(
                settings.seed, frame_number, block_rows, block_columns, component
            )
            offsets[..., component] = (2.0 * random_values - 1.0) * settings.diffusion
        float32_limit = np.finfo(np.float32).max
        representatives = np.clip(
            representatives.astype(np.float64) + offsets, -float32_limit, float32_limit
        ).astype(np.float32)
    if settings.refresh_probability > 0.0:
        refresh = (
            _unit_random_grid(settings.seed, frame_number, block_rows, block_columns, 2)
            < settings.refresh_probability
        )
    else:
        refresh = np.zeros(representatives.shape[:2], dtype=np.bool_)
    return PreparedBlocks(representatives, refresh, effective_block_size, (height, width))


def _validate_arrays(motion: FloatImage, matte: FloatMask) -> None:
    for name, array in (("motion", motion), ("matte", matte)):
        if not isinstance(array, np.ndarray):
            raise TypeError(f"{name} must be a NumPy array")
        if array.dtype != np.float32:
            raise TypeError(f"{name} must use float32")
        if not np.all(np.isfinite(array)):
            raise ValueError(f"{name} must contain only finite values")
    if motion.ndim != 3 or motion.shape[2] != 4:
        raise ValueError("motion must have shape (height, width, 4)")
    if matte.shape != motion.shape[:2]:
        raise ValueError("matte must match motion shape (height, width)")
    if matte.ndim != 2:
        raise ValueError("matte must match motion shape (height, width)")
    if matte.shape[0] == 0 or matte.shape[1] == 0:
        raise ValueError("frame dimensions must be nonzero")
    if np.any((matte < 0.0) | (matte > 1.0)):
        raise ValueError("matte coverage must be between 0 and 1")


_UINT64_MASK = (1 << 64) - 1


def _unit_random_grid(
    seed: int,
    frame_number: int,
    block_rows: int,
    block_columns: int,
    stream: int,
) -> NDArray[np.float64]:
    """Map deterministic integer block coordinates to floats in ``[0, 1)``."""
    block_y = np.arange(block_rows, dtype=np.uint64)[:, None]
    block_x = np.arange(block_columns, dtype=np.uint64)[None, :]
    value = np.full((block_rows, block_columns), int(seed) & _UINT64_MASK, dtype=np.uint64)
    value ^= np.uint64((int(frame_number) * 0xD6E8FEB86659FD93) & _UINT64_MASK)
    value ^= block_y * np.uint64(0xA5A3564E27F8862F)
    value ^= block_x * np.uint64(0x9E3779B97F4A7C15)
    value ^= np.uint64((stream * 0x94D049BB133111EB) & _UINT64_MASK)
    value += np.uint64(0x9E3779B97F4A7C15)
    value = (value ^ (value >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    value = (value ^ (value >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    value ^= value >> np.uint64(31)
    return (value >> np.uint64(11)).astype(np.float64) * (1.0 / (1 << 53))


def _block_reduce(displacement: FloatImage, matte: FloatMask, block_size: int) -> FloatImage:
    y_starts = np.arange(0, matte.shape[0], block_size)
    x_starts = np.arange(0, matte.shape[1], block_size)
    weights = matte.astype(np.float64)
    weighted_vectors = displacement.astype(np.float64) * weights[..., None]
    block_weights = np.add.reduceat(np.add.reduceat(weights, y_starts, axis=0), x_starts, axis=1)
    block_vectors = np.add.reduceat(
        np.add.reduceat(weighted_vectors, y_starts, axis=0), x_starts, axis=1
    )
    return np.divide(
        block_vectors,
        block_weights[..., None],
        out=np.zeros_like(block_vectors),
        where=block_weights[..., None] > 0.0,
    ).astype(np.float32)
