"""Pure NumPy hard-localized and selected-object trail feedback processing."""

from numbers import Integral

import numpy as np
from numpy.typing import NDArray

from .block_preparation import prepare_blocks
from .contracts import (
    FeedbackMode,
    FeedbackSettings,
    FeedbackState,
    FloatImage,
    FloatMask,
)
from .sampling import bilinear_sample


def _expand_blocks(block_values: NDArray, block_size: int, height: int, width: int) -> NDArray:
    """Expand a compact block grid over pixels and trim partial edge blocks."""
    return np.repeat(np.repeat(block_values, block_size, axis=0), block_size, axis=1)[
        :height, :width
    ]


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
        prepared_blocks = prepare_blocks(motion, matte, frame_number, settings)

        height, width = matte.shape
        displacement = _expand_blocks(
            prepared_blocks.displacement, prepared_blocks.block_size, height, width
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
        if settings.mode is FeedbackMode.TRAIL:
            decayed_history_matte = settings.trail_decay * warped_matte * covered
            next_matte = np.maximum(matte, decayed_history_matte).astype(np.float32, copy=False)
            localized_history = matte * warped_matte + (1.0 - matte) * decayed_history_matte
        else:
            next_matte = matte
            localized_history = matte * warped_matte
        refreshed = _expand_blocks(
            prepared_blocks.refresh, prepared_blocks.block_size, height, width
        )
        blend = (settings.persistence * localized_history * covered * ~refreshed)[..., None]
        output = (beauty * (1.0 - blend) + warped_history * blend).astype(np.float32, copy=False)
    if previous_state is None or force_reset:
        next_matte = matte
    state = FeedbackState(output.copy(), next_matte.copy(), frame_number)
    return output, state
