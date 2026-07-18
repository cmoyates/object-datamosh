import numpy as np
import pytest

from object_datamosh.core.contracts import (
    FeedbackSettings,
    FeedbackState,
    MatteSource,
    MotionChannels,
)


def test_feedback_settings_have_conservative_stable_defaults() -> None:
    settings = FeedbackSettings()

    assert settings.persistence == 0.85
    assert settings.block_size == 16
    assert settings.motion_channels is MotionChannels.RG
    assert settings.motion_gain == 1.0
    assert settings.motion_clamp == 64.0
    assert settings.motion_quantization == 1.0
    assert settings.diffusion == 0.0
    assert settings.refresh_probability == 0.0
    assert settings.seed == 0
    assert settings.matte_source is MatteSource.OBJECT_INDEX


def test_feedback_settings_reject_values_outside_probability_range() -> None:
    with pytest.raises(ValueError, match="persistence must be between 0 and 1"):
        FeedbackSettings(persistence=1.1)


def test_feedback_state_accepts_scene_linear_float32_rgba_history() -> None:
    history = np.zeros((3, 5, 4), dtype=np.float32)
    matte = np.ones((3, 5), dtype=np.float32)

    state = FeedbackState(history=history, history_matte=matte, frame_number=7)

    assert state.history.shape == (3, 5, 4)
    assert state.history_matte.shape == (3, 5)
    assert state.frame_number == 7


def test_feedback_state_rejects_non_float32_history() -> None:
    history = np.zeros((3, 5, 4), dtype=np.float64)
    matte = np.ones((3, 5), dtype=np.float32)

    with pytest.raises(TypeError, match="history must use float32"):
        FeedbackState(history=history, history_matte=matte, frame_number=7)


def test_feedback_state_rejects_incompatible_history_shapes() -> None:
    history = np.zeros((3, 5, 3), dtype=np.float32)
    matte = np.ones((3, 4), dtype=np.float32)

    with pytest.raises(ValueError, match=r"history must have shape \(height, width, 4\)"):
        FeedbackState(history=history, history_matte=matte, frame_number=7)
