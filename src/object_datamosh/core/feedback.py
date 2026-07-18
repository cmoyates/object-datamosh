"""Pure NumPy hard-localized temporal feedback processing."""

import numpy as np
from numpy.typing import NDArray

from .contracts import FeedbackSettings, FeedbackState, FloatImage, FloatMask, MotionChannels
from .sampling import bilinear_sample


def _block_reduce_expand(displacement: FloatImage, matte: FloatMask, block_size: int) -> FloatImage:
    """Return one matte-weighted representative displacement per partial edge block."""
    height, width = matte.shape
    expanded = np.zeros_like(displacement, dtype=np.float32)
    for y0 in range(0, height, block_size):
        y1 = min(y0 + block_size, height)
        for x0 in range(0, width, block_size):
            x1 = min(x0 + block_size, width)
            weights = np.clip(matte[y0:y1, x0:x1], 0.0, 1.0)
            weight_sum = float(weights.sum(dtype=np.float64))
            if weight_sum > 0.0:
                vectors = displacement[y0:y1, x0:x1]
                representative = (
                    (vectors * weights[..., None]).sum(axis=(0, 1), dtype=np.float64) / weight_sum
                ).astype(np.float32)
                expanded[y0:y1, x0:x1] = representative
    return expanded


_UINT64_MASK = (1 << 64) - 1


def _unit_random(seed: int, frame_number: int, block_y: int, block_x: int, stream: int) -> float:
    """Map deterministic integer coordinates to a float in ``[0, 1)``."""
    value = seed & _UINT64_MASK
    value ^= (frame_number * 0xD6E8FEB86659FD93) & _UINT64_MASK
    value ^= (block_y * 0xA5A3564E27F8862F) & _UINT64_MASK
    value ^= (block_x * 0x9E3779B97F4A7C15) & _UINT64_MASK
    value ^= (stream * 0x94D049BB133111EB) & _UINT64_MASK
    value = (value + 0x9E3779B97F4A7C15) & _UINT64_MASK
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _UINT64_MASK
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _UINT64_MASK
    value ^= value >> 31
    return (value >> 11) * (1.0 / (1 << 53))


def _apply_block_randomness(
    displacement: FloatImage,
    block_size: int,
    diffusion: float,
    refresh_probability: float,
    seed: int,
    frame_number: int,
) -> tuple[FloatImage, NDArray[np.bool_]]:
    height, width = displacement.shape[:2]
    result = displacement.copy()
    refreshed = np.zeros((height, width), dtype=np.bool_)
    for y0 in range(0, height, block_size):
        y1 = min(y0 + block_size, height)
        block_y = y0 // block_size
        for x0 in range(0, width, block_size):
            x1 = min(x0 + block_size, width)
            block_x = x0 // block_size
            if diffusion > 0.0:
                noise_x = 2.0 * _unit_random(seed, frame_number, block_y, block_x, 0) - 1.0
                noise_y = 2.0 * _unit_random(seed, frame_number, block_y, block_x, 1) - 1.0
                result[y0:y1, x0:x1, 0] += np.float32(noise_x * diffusion)
                result[y0:y1, x0:x1, 1] += np.float32(noise_y * diffusion)
            if (
                refresh_probability > 0.0
                and _unit_random(seed, frame_number, block_y, block_x, 2) < refresh_probability
            ):
                refreshed[y0:y1, x0:x1] = True
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
    if motion.shape != beauty.shape:
        raise ValueError("motion must match beauty shape (height, width, 4)")
    if matte.shape != beauty.shape[:2]:
        raise ValueError("matte must match beauty shape (height, width)")
    if np.any((matte < 0.0) | (matte > 1.0)):
        raise ValueError("matte coverage must be between 0 and 1")
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
    _validate_inputs(beauty, motion, matte, previous_state)
    if previous_state is None or force_reset:
        output = beauty.copy()
    else:
        channels = (0, 1) if settings.motion_channels is MotionChannels.RG else (2, 3)
        displacement = motion[..., channels].astype(np.float32, copy=True)
        displacement *= settings.motion_gain
        if settings.reverse_motion:
            displacement *= -1.0
        if settings.flip_x:
            displacement[..., 0] *= -1.0
        if settings.flip_y:
            displacement[..., 1] *= -1.0
        lengths = np.linalg.norm(displacement, axis=-1)
        scales = np.minimum(1.0, settings.motion_clamp / np.maximum(lengths, 1e-12))
        displacement *= scales[..., None]
        displacement = _block_reduce_expand(displacement, matte, settings.block_size)
        if settings.motion_quantization > 0.0:
            step = settings.motion_quantization
            displacement = (np.rint(displacement / step) * step).astype(np.float32, copy=False)
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
        history_pixel_valid = (
            np.all(np.isfinite(previous_state.history), axis=-1)
            & np.isfinite(previous_state.history_matte)
            & (previous_state.history_matte > 0.0)
            & (previous_state.history_matte <= 1.0)
        )
        valid_history_matte = np.where(
            history_pixel_valid, previous_state.history_matte, 0.0
        ).astype(np.float32, copy=False)
        safe_history = np.where(history_pixel_valid[..., None], previous_state.history, 0.0)
        premultiplied = safe_history * valid_history_matte[..., None]
        warped_premultiplied, valid = bilinear_sample(premultiplied, sample_x, sample_y)
        warped_matte, _ = bilinear_sample(valid_history_matte, sample_x, sample_y)
        covered = valid & (warped_matte > 1e-6)
        safe_matte = np.where(covered, warped_matte, 1.0)
        warped_history = warped_premultiplied / safe_matte[..., None]
        blend = (settings.persistence * matte * warped_matte * covered * ~refreshed)[..., None]
        output = (beauty * (1.0 - blend) + warped_history * blend).astype(np.float32, copy=False)
    state = FeedbackState(output.copy(), matte.copy(), frame_number)
    return output, state
