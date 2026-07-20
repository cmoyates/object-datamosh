"""Pure, deterministic artistic starting configurations."""

from .contracts import (
    FeedbackMode,
    FeedbackSettings,
    HistorySource,
    InvalidHistoryFallback,
)


def extreme_full_frame_feedback_settings() -> FeedbackSettings:
    """Return the auditable Extreme Full-Frame Feedback configuration."""
    return FeedbackSettings(
        history_source=HistorySource.FULL_FRAME,
        invalid_history_fallback=InvalidHistoryFallback.SAME_PIXEL_HISTORY,
        mode=FeedbackMode.TRAIL,
        persistence=1.0,
        trail_decay=0.995,
        trail_motion_mix=0.1,
        refresh_probability=0.0,
        block_size=32,
        motion_quantization=8.0,
        diffusion=6.0,
    )
