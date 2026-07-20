from typing import Any, cast

import numpy as np
import pytest

from object_datamosh.core.contracts import (
    FeedbackMode,
    FeedbackSettings,
    FeedbackState,
    HistorySource,
    MatteSource,
    MotionChannels,
)


def test_feedback_settings_have_conservative_stable_defaults() -> None:
    settings = FeedbackSettings()

    assert settings.mode is FeedbackMode.HARD_LOCALIZED
    assert settings.history_source is HistorySource.TARGET_ONLY
    assert settings.trail_decay == 0.85
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


def test_feedback_settings_supports_full_frame_trail() -> None:
    settings = FeedbackSettings(
        mode=FeedbackMode.TRAIL,
        history_source=HistorySource.FULL_FRAME,
    )

    assert settings.mode is FeedbackMode.TRAIL
    assert settings.history_source is HistorySource.FULL_FRAME


def test_feedback_settings_reject_values_outside_probability_range() -> None:
    with pytest.raises(ValueError, match="persistence must be between 0 and 1"):
        FeedbackSettings(persistence=1.1)
    with pytest.raises(ValueError, match="trail_decay must be between 0 and 1"):
        FeedbackSettings(trail_decay=-0.1)


@pytest.mark.parametrize(
    "field",
    [
        "trail_decay",
        "persistence",
        "motion_gain",
        "motion_clamp",
        "motion_quantization",
        "diffusion",
        "refresh_probability",
    ],
)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_feedback_settings_reject_non_finite_float_controls(field: str, value: float) -> None:
    arguments: dict[str, Any] = {field: value}

    with pytest.raises(ValueError, match=rf"{field} must be finite"):
        FeedbackSettings(**arguments)


@pytest.mark.parametrize("value", [1.5, True])
def test_feedback_settings_require_an_integral_non_boolean_block_size(value: object) -> None:
    with pytest.raises(TypeError, match="block_size must be an integer"):
        FeedbackSettings(block_size=cast(Any, value))


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
