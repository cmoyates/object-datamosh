"""Pure NumPy hard-localized and selected-object trail feedback processing."""

from numbers import Integral

import numpy as np
from numpy.typing import NDArray

from .block_preparation import PreparedBlocks, prepare_blocks
from .contracts import (
    FeedbackMode,
    FeedbackSettings,
    FeedbackState,
    FloatImage,
    FloatMask,
    HistorySource,
    InvalidHistoryFallback,
)
from .diagnostics import CHANGE_EPSILON, FrameDiagnostics
from .sampling import bilinear_sample


def _expand_blocks(block_values: NDArray, block_size: int, height: int, width: int) -> NDArray:
    """Expand a compact block grid over pixels and trim partial edge blocks."""
    return np.repeat(np.repeat(block_values, block_size, axis=0), block_size, axis=1)[
        :height, :width
    ]


def _apply_refresh(
    prepared_blocks: PreparedBlocks,
    candidate: NDArray[np.bool_],
    covered: NDArray[np.bool_],
    localized_history: FloatMask,
    persistence: float,
) -> tuple[NDArray[np.float32], NDArray[np.bool_], int]:
    """Apply selected refresh blocks and return blend weights plus diagnostics."""
    height, width = candidate.shape
    unrefreshed_blend = persistence * localized_history * covered
    if not np.any(prepared_blocks.refresh):
        return (
            unrefreshed_blend[..., None],
            np.zeros(candidate.shape, dtype=bool),
            0,
        )

    refreshed = _expand_blocks(
        prepared_blocks.refresh, prepared_blocks.block_size, height, width
    ).astype(bool, copy=False)
    active_pixels = candidate & covered & (persistence > 0.0)
    y_starts = np.arange(0, height, prepared_blocks.block_size)
    x_starts = np.arange(0, width, prepared_blocks.block_size)
    block_candidates = np.logical_or.reduceat(
        np.logical_or.reduceat(active_pixels, y_starts, axis=0), x_starts, axis=1
    )
    refresh_blocks = int(np.count_nonzero(prepared_blocks.refresh & block_candidates))
    refresh_restored = refreshed & (unrefreshed_blend > 0.0)
    blend = (unrefreshed_blend * ~refreshed)[..., None]
    return blend, refresh_restored, refresh_blocks


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
    output, state, _diagnostics = process_frame_with_diagnostics(
        beauty,
        motion,
        matte,
        previous_state,
        frame_number,
        settings,
        force_reset,
    )
    return output, state


def process_frame_with_diagnostics(
    beauty: FloatImage,
    motion: FloatImage,
    matte: FloatMask,
    previous_state: FeedbackState | None,
    frame_number: int,
    settings: FeedbackSettings,
    force_reset: bool = False,
) -> tuple[FloatImage, FeedbackState, FrameDiagnostics]:
    """Process one frame and return diagnostics captured from its actual pixel decisions."""
    if isinstance(frame_number, bool) or not isinstance(frame_number, Integral):
        raise TypeError("frame_number must be an integer")
    if not isinstance(settings, FeedbackSettings):
        raise TypeError("settings must be a FeedbackSettings value")
    if not isinstance(force_reset, bool):
        raise TypeError("force_reset must be a boolean")
    _validate_inputs(beauty, motion, matte, previous_state)
    reset = previous_state is None or force_reset
    primary_attempt = np.zeros(matte.shape, dtype=bool)
    primary_valid = np.zeros(matte.shape, dtype=bool)
    fallback_attempt = np.zeros(matte.shape, dtype=bool)
    fallback_valid = np.zeros(matte.shape, dtype=bool)
    refresh_restored = np.zeros(matte.shape, dtype=bool)
    blend = np.zeros((*matte.shape, 1), dtype=np.float32)
    refresh_blocks = 0
    if reset:
        output = beauty.copy()
        next_matte = matte
        localized_history = matte
    else:
        prepared_blocks = prepare_blocks(motion, matte, frame_number, settings)

        height, width = matte.shape
        displacement = _expand_blocks(
            prepared_blocks.displacement, prepared_blocks.block_size, height, width
        )
        sample_y, sample_x = np.indices(matte.shape, dtype=np.float32)
        sample_x -= displacement[..., 0]
        sample_y -= displacement[..., 1]
        if settings.history_source is HistorySource.FULL_FRAME:
            history_color_valid = np.all(np.isfinite(previous_state.history), axis=-1)
            safe_history = np.where(history_color_valid[..., None], previous_state.history, 0.0)
            warped_history, valid = bilinear_sample(safe_history, sample_x, sample_y)
            warped_invalid, _ = bilinear_sample(
                (~history_color_valid).astype(np.float32), sample_x, sample_y
            )
            covered = valid & (warped_invalid == 0.0) & np.all(np.isfinite(warped_history), axis=-1)
            primary_covered = covered.copy()
            if settings.invalid_history_fallback is InvalidHistoryFallback.SAME_PIXEL_HISTORY:
                screen_y, screen_x = np.indices(matte.shape, dtype=np.float32)
                screen_history, screen_valid = bilinear_sample(safe_history, screen_x, screen_y)
                screen_invalid, _ = bilinear_sample(
                    (~history_color_valid).astype(np.float32), screen_x, screen_y
                )
                screen_covered = (
                    screen_valid
                    & (screen_invalid == 0.0)
                    & np.all(np.isfinite(screen_history), axis=-1)
                )
                use_screen = ~covered & screen_covered
                warped_history = np.where(use_screen[..., None], screen_history, warped_history)
                covered = covered | screen_covered
            else:
                use_screen = np.zeros(matte.shape, dtype=bool)
            warped_history = np.where(covered[..., None], warped_history, 0.0)
            if settings.mode is FeedbackMode.TRAIL:
                history_matte_valid = (
                    np.isfinite(previous_state.history_matte)
                    & (previous_state.history_matte >= 0.0)
                    & (previous_state.history_matte <= 1.0)
                )
                safe_history_matte = np.where(
                    history_matte_valid, previous_state.history_matte, 0.0
                ).astype(np.float32, copy=False)
                warped_matte, matte_sample_valid = bilinear_sample(
                    safe_history_matte, sample_x, sample_y
                )
                warped_matte_invalid, _ = bilinear_sample(
                    (~history_matte_valid).astype(np.float32), sample_x, sample_y
                )
                effect_sample_valid = (
                    matte_sample_valid & (warped_matte_invalid == 0.0) & np.isfinite(warped_matte)
                )
                motion_mask = warped_matte * effect_sample_valid
                screen_mask = safe_history_matte
                propagated_mask = np.clip(
                    (1.0 - settings.trail_motion_mix) * screen_mask
                    + settings.trail_motion_mix * motion_mask,
                    0.0,
                    1.0,
                )
                trail_mask = settings.trail_decay * propagated_mask
                next_matte = np.clip(np.maximum(matte, trail_mask), 0.0, 1.0).astype(
                    np.float32, copy=False
                )
                localized_history = next_matte
            else:
                next_matte = matte
                localized_history = matte
        else:
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
            valid_history_matte = np.where(
                history_covered, previous_state.history_matte, 0.0
            ).astype(np.float32, copy=False)
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
            primary_covered = covered.copy()
            use_screen = np.zeros(matte.shape, dtype=bool)
        candidate = localized_history > 0.0
        primary_attempt = candidate
        primary_valid = candidate & primary_covered
        fallback_attempt = candidate & ~primary_covered
        fallback_valid = candidate & use_screen
        blend, refresh_restored, refresh_blocks = _apply_refresh(
            prepared_blocks,
            candidate,
            covered,
            localized_history,
            settings.persistence,
        )
        output = (beauty * (1.0 - blend) + warped_history * blend).astype(np.float32, copy=False)
    state = FeedbackState(output.copy(), next_matte.copy(), frame_number)
    pixel_change = np.max(np.abs(output[..., :3] - beauty[..., :3]), axis=-1)
    changed = pixel_change > CHANGE_EPSILON
    diagnostics = FrameDiagnostics(
        frame_number=int(frame_number),
        reset=reset,
        pixel_count=matte.size,
        target_matte_pixels=int(np.count_nonzero(matte > 0.0)),
        target_matte_coverage=float(np.mean(matte, dtype=np.float64)),
        effect_matte_pixels=int(np.count_nonzero(localized_history > 0.0)),
        effect_matte_coverage=float(np.mean(localized_history, dtype=np.float64)),
        primary_history_attempts=int(np.count_nonzero(primary_attempt)),
        primary_history_valid_uses=int(np.count_nonzero(primary_valid)),
        primary_history_invalid_samples=int(np.count_nonzero(primary_attempt & ~primary_valid)),
        same_pixel_fallback_attempts=(
            int(np.count_nonzero(fallback_attempt))
            if settings.invalid_history_fallback is InvalidHistoryFallback.SAME_PIXEL_HISTORY
            else 0
        ),
        same_pixel_fallback_valid_uses=int(np.count_nonzero(fallback_valid)),
        current_beauty_fallback_pixels=int(
            np.count_nonzero(primary_attempt & ~primary_valid & ~fallback_valid)
        ),
        refresh_restored_pixels=int(np.count_nonzero(refresh_restored)),
        refresh_restored_blocks=refresh_blocks,
        historical_blend_pixels=int(np.count_nonzero(blend[..., 0] > 0.0)),
        historical_blend_weight=float(np.sum(blend[..., 0], dtype=np.float64)),
        changed_output_pixels=int(np.count_nonzero(changed)),
        changed_output_ratio=float(np.count_nonzero(changed) / matte.size),
        changed_output_mean_absolute=float(np.mean(pixel_change, dtype=np.float64)),
        changed_output_max_absolute=float(np.max(pixel_change)),
    )
    return output, state, diagnostics
