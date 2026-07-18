"""Pure NumPy hard-localized temporal feedback processing."""

from numbers import Integral

import numpy as np
from numpy.typing import NDArray

from .contracts import FeedbackSettings, FeedbackState, FloatImage, FloatMask, MotionChannels
from .sampling import bilinear_sample


def _block_reduce_expand(displacement: FloatImage, matte: FloatMask, block_size: int) -> FloatImage:
    """Return one matte-weighted representative displacement per partial edge block."""
    height, width = matte.shape
    y_starts = np.arange(0, height, block_size)
    x_starts = np.arange(0, width, block_size)
    weights = np.clip(matte, 0.0, 1.0).astype(np.float64)
    weighted_vectors = displacement.astype(np.float64) * weights[..., None]
    block_weights = np.add.reduceat(np.add.reduceat(weights, y_starts, axis=0), x_starts, axis=1)
    block_vectors = np.add.reduceat(
        np.add.reduceat(weighted_vectors, y_starts, axis=0), x_starts, axis=1
    )
    representatives = np.divide(
        block_vectors,
        block_weights[..., None],
        out=np.zeros_like(block_vectors),
        where=block_weights[..., None] > 0.0,
    ).astype(np.float32)
    return np.repeat(np.repeat(representatives, block_size, axis=0), block_size, axis=1)[
        :height, :width
    ]


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
    value = np.full((block_rows, block_columns), seed & _UINT64_MASK, dtype=np.uint64)
    value ^= np.uint64((frame_number * 0xD6E8FEB86659FD93) & _UINT64_MASK)
    value ^= block_y * np.uint64(0xA5A3564E27F8862F)
    value ^= block_x * np.uint64(0x9E3779B97F4A7C15)
    value ^= np.uint64((stream * 0x94D049BB133111EB) & _UINT64_MASK)
    value += np.uint64(0x9E3779B97F4A7C15)
    value = (value ^ (value >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    value = (value ^ (value >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    value ^= value >> np.uint64(31)
    return (value >> np.uint64(11)).astype(np.float64) * (1.0 / (1 << 53))


def _expand_blocks(block_values: NDArray, block_size: int, height: int, width: int) -> NDArray:
    """Expand a block grid over pixels and trim partial edge blocks."""
    return np.repeat(np.repeat(block_values, block_size, axis=0), block_size, axis=1)[
        :height, :width
    ]


def _apply_block_randomness(
    displacement: FloatImage,
    block_size: int,
    diffusion: float,
    refresh_probability: float,
    seed: int,
    frame_number: int,
) -> tuple[FloatImage, NDArray[np.bool_]]:
    height, width = displacement.shape[:2]
    block_rows = (height + block_size - 1) // block_size
    block_columns = (width + block_size - 1) // block_size
    block_shape = (block_rows, block_columns)
    if diffusion > 0.0:
        block_offsets = np.empty((*block_shape, 2), dtype=np.float64)
        for component in range(2):
            random_values = _unit_random_grid(
                seed, frame_number, block_rows, block_columns, component
            )
            block_offsets[..., component] = (2.0 * random_values - 1.0) * diffusion
        offsets = _expand_blocks(block_offsets, block_size, height, width)
        float32_limit = np.finfo(np.float32).max
        result = np.clip(displacement.astype(np.float64) + offsets, -float32_limit, float32_limit)
        result = result.astype(np.float32)
    else:
        result = displacement.copy()
    if refresh_probability > 0.0:
        block_refresh = (
            _unit_random_grid(seed, frame_number, block_rows, block_columns, 2)
            < refresh_probability
        )
        refreshed = _expand_blocks(block_refresh, block_size, height, width)
    else:
        refreshed = np.zeros((height, width), dtype=np.bool_)
    return result, refreshed


def _validate_inputs(
    beauty: FloatImage,
    motion: FloatImage,
    matte: FloatMask,
    previous_state: FeedbackState | None,
) -> None:
    for name, array in (("beauty", beauty), ("motion", motion), ("matte", matte)):
        if not isinstance(array, np.ndarray):
            raise TypeError(f"{name} must be a NumPy array")
        if array.dtype != np.float32:
            raise TypeError(f"{name} must use float32")
        if not np.all(np.isfinite(array)):
            raise ValueError(f"{name} must contain only finite values")
    if beauty.ndim != 3 or beauty.shape[2] != 4:
        raise ValueError("beauty must have shape (height, width, 4)")
    if beauty.shape[0] == 0 or beauty.shape[1] == 0:
        raise ValueError("frame dimensions must be nonzero")
    if motion.shape != beauty.shape:
        raise ValueError("motion must match beauty shape (height, width, 4)")
    if matte.shape != beauty.shape[:2]:
        raise ValueError("matte must match beauty shape (height, width)")
    if np.any((matte < 0.0) | (matte > 1.0)):
        raise ValueError("matte coverage must be between 0 and 1")
    if previous_state is not None and not isinstance(previous_state, FeedbackState):
        raise TypeError("previous_state must be a FeedbackState value or None")
    if previous_state is not None and previous_state.history.shape != beauty.shape:
        raise ValueError("previous state dimensions must match the current frame")


def process_frame(
    beauty: FloatImage,
    motion: FloatImage,
    matte: FloatMask,
    previous_state: FeedbackState | None,
    frame_number: int,
    settings: FeedbackSettings,
    force_reset: bool = False,
) -> tuple[FloatImage, FeedbackState]:
    """Process one frame and return its RGBA output plus state for the next frame."""
    if isinstance(frame_number, bool) or not isinstance(frame_number, Integral):
        raise TypeError("frame_number must be an integer")
    if not isinstance(settings, FeedbackSettings):
        raise TypeError("settings must be a FeedbackSettings value")
    if not isinstance(force_reset, bool):
        raise TypeError("force_reset must be a boolean")
    _validate_inputs(beauty, motion, matte, previous_state)
    if previous_state is None or force_reset:
        output = beauty.copy()
    else:
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
        displacement = _block_reduce_expand(displacement, matte, settings.block_size)
        smallest_float32 = float(np.nextafter(np.float32(0.0), np.float32(1.0)))
        if settings.motion_quantization >= smallest_float32:
            step = settings.motion_quantization
            quantized = np.rint(displacement.astype(np.float64) / step) * step
            float32_limit = np.finfo(np.float32).max
            displacement = np.clip(quantized, -float32_limit, float32_limit).astype(np.float32)
        displacement, refreshed = _apply_block_randomness(
            displacement,
            settings.block_size,
            settings.diffusion,
            settings.refresh_probability,
            settings.seed,
            frame_number,
        )

        sample_y, sample_x = np.indices(matte.shape, dtype=np.float32)
        sample_x -= displacement[..., 0]
        sample_y -= displacement[..., 1]
        history_matte_valid = (
            np.isfinite(previous_state.history_matte)
            & (previous_state.history_matte >= 0.0)
            & (previous_state.history_matte <= 1.0)
        )
        history_color_valid = np.all(np.isfinite(previous_state.history), axis=-1)
        history_covered = (
            history_matte_valid & history_color_valid & (previous_state.history_matte > 0.0)
        )
        invalid_covered_history = ~history_matte_valid | (
            (previous_state.history_matte > 0.0) & ~history_color_valid
        )
        valid_history_matte = np.where(history_covered, previous_state.history_matte, 0.0).astype(
            np.float32, copy=False
        )
        safe_history = np.where(history_covered[..., None], previous_state.history, 0.0)
        premultiplied = safe_history * valid_history_matte[..., None]
        warped_premultiplied, valid = bilinear_sample(premultiplied, sample_x, sample_y)
        warped_matte, _ = bilinear_sample(valid_history_matte, sample_x, sample_y)
        warped_invalid, _ = bilinear_sample(
            invalid_covered_history.astype(np.float32), sample_x, sample_y
        )
        covered = valid & (warped_matte > 0.0) & (warped_invalid == 0.0)
        safe_matte = np.where(covered, warped_matte, 1.0)
        warped_history = warped_premultiplied / safe_matte[..., None]
        blend = (settings.persistence * matte * warped_matte * covered * ~refreshed)[..., None]
        output = (beauty * (1.0 - blend) + warped_history * blend).astype(np.float32, copy=False)
    state = FeedbackState(output.copy(), matte.copy(), frame_number)
    return output, state
