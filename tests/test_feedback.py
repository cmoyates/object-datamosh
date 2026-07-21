import hashlib

import numpy as np
import pytest

from object_datamosh.core import feedback
from object_datamosh.core.contracts import (
    FeedbackMode,
    FeedbackSettings,
    FeedbackState,
    HistorySource,
    InvalidHistoryFallback,
    MotionChannels,
)
from object_datamosh.core.feedback import process_frame
from object_datamosh.core.presets import extreme_full_frame_feedback_settings


def _rgba(height: int, width: int, value: float) -> np.ndarray:
    return np.full((height, width, 4), value, dtype=np.float32)


def _motion(height: int, width: int) -> np.ndarray:
    return np.zeros((height, width, 4), dtype=np.float32)


def test_first_frame_initializes_clean_history() -> None:
    beauty = _rgba(2, 3, 0.25)
    matte = np.array([[0.0, 0.5, 1.0], [1.0, 0.5, 0.0]], dtype=np.float32)

    output, state = process_frame(
        beauty=beauty,
        motion=_motion(2, 3),
        matte=matte,
        previous_state=None,
        frame_number=12,
        settings=FeedbackSettings(),
    )

    np.testing.assert_array_equal(output, beauty)
    np.testing.assert_array_equal(state.history, beauty)
    np.testing.assert_array_equal(state.history_matte, matte)
    assert state.frame_number == 12
    assert output is not beauty


@pytest.mark.parametrize("persistence,refresh_probability", [(0.0, 0.0), (1.0, 1.0)])
@pytest.mark.parametrize("fallback", list(InvalidHistoryFallback))
@pytest.mark.parametrize("history_source", list(HistorySource))
def test_empty_hard_frame_skips_motion_and_sampling(
    history_source: HistorySource,
    fallback: InvalidHistoryFallback,
    persistence: float,
    refresh_probability: float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    height, width = 3, 5
    beauty = _rgba(height, width, 0.25)
    previous = FeedbackState(
        history=_rgba(height, width, 1.0),
        history_matte=np.ones((height, width), dtype=np.float32),
        frame_number=1,
    )

    def unexpected_call(*_args: object, **_kwargs: object) -> None:
        pytest.fail("empty Hard frames must not prepare motion or sample history")

    monkeypatch.setattr(feedback, "prepare_blocks", unexpected_call)
    monkeypatch.setattr(feedback, "sample_with_plan", unexpected_call)

    output, state, diagnostics = feedback.process_frame_with_diagnostics(
        beauty=beauty,
        motion=_motion(height, width),
        matte=np.zeros((height, width), dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(
            mode=FeedbackMode.HARD_LOCALIZED,
            history_source=history_source,
            invalid_history_fallback=fallback,
            persistence=persistence,
            refresh_probability=refresh_probability,
        ),
    )

    np.testing.assert_array_equal(output, beauty)
    np.testing.assert_array_equal(state.history, beauty)
    np.testing.assert_array_equal(state.history_matte, np.zeros((height, width), dtype=np.float32))
    assert state.frame_number == 2
    assert diagnostics.reset is False
    assert diagnostics.target_matte_pixels == 0
    assert diagnostics.effect_matte_pixels == 0
    assert diagnostics.primary_history_attempts == 0
    assert diagnostics.historical_blend_pixels == 0
    assert diagnostics.refresh_restored_pixels == 0
    assert diagnostics.changed_output_pixels == 0


@pytest.mark.parametrize("height,width", [(1, 1), (3, 5)])
@pytest.mark.parametrize("history_source", list(HistorySource))
def test_empty_trail_with_empty_valid_history_skips_motion_and_sampling(
    height: int,
    width: int,
    history_source: HistorySource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    beauty = _rgba(height, width, 0.4)
    previous = FeedbackState(
        history=_rgba(height, width, 0.9),
        history_matte=np.zeros((height, width), dtype=np.float32),
        frame_number=6,
    )

    def unexpected_call(*_args: object, **_kwargs: object) -> None:
        pytest.fail("eligible empty Trail frames must not prepare motion or sample history")

    monkeypatch.setattr(feedback, "prepare_blocks", unexpected_call)
    monkeypatch.setattr(feedback, "sample_with_plan", unexpected_call)

    output, state, diagnostics = feedback.process_frame_with_diagnostics(
        beauty,
        _motion(height, width),
        np.zeros((height, width), dtype=np.float32),
        previous,
        frame_number=7,
        settings=FeedbackSettings(
            mode=FeedbackMode.TRAIL,
            history_source=history_source,
            persistence=0.0,
            trail_decay=0.0,
            refresh_probability=1.0,
        ),
    )

    np.testing.assert_array_equal(output, beauty)
    np.testing.assert_array_equal(state.history_matte, np.zeros((height, width), dtype=np.float32))
    assert diagnostics.reset is False
    assert diagnostics.effect_matte_coverage == 0.0
    assert diagnostics.primary_history_attempts == 0
    assert diagnostics.changed_output_mean_absolute == 0.0
    assert diagnostics.changed_output_max_absolute == 0.0


@pytest.mark.parametrize(
    "prior_coverage",
    [
        np.array([[1.0]], dtype=np.float32),
        np.array([[np.nan]], dtype=np.float32),
        np.array([[np.inf]], dtype=np.float32),
        np.array([[-0.1]], dtype=np.float32),
        np.array([[1.1]], dtype=np.float32),
    ],
)
@pytest.mark.parametrize("history_source", list(HistorySource))
def test_empty_trail_does_not_skip_nonempty_or_invalid_history_coverage(
    prior_coverage: np.ndarray,
    history_source: HistorySource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"prepare": 0, "sample": 0}
    real_prepare = feedback.prepare_blocks
    real_sample = feedback.sample_with_plan

    def recording_prepare(
        motion: np.ndarray,
        matte: np.ndarray,
        frame_number: int,
        settings: FeedbackSettings,
    ) -> object:
        calls["prepare"] += 1
        return real_prepare(motion, matte, frame_number, settings)

    def recording_sample(image: np.ndarray, plan: object) -> object:
        calls["sample"] += 1
        return real_sample(image, plan)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(feedback, "prepare_blocks", recording_prepare)
    monkeypatch.setattr(feedback, "sample_with_plan", recording_sample)
    beauty = _rgba(1, 1, 0.2)

    output, state, diagnostics = feedback.process_frame_with_diagnostics(
        beauty,
        _motion(1, 1),
        np.zeros((1, 1), dtype=np.float32),
        FeedbackState(_rgba(1, 1, 0.8), prior_coverage, frame_number=1),
        frame_number=2,
        settings=FeedbackSettings(
            mode=FeedbackMode.TRAIL,
            history_source=history_source,
            persistence=1.0,
            trail_decay=1.0,
            block_size=1,
        ),
    )

    assert calls["prepare"] == 1
    assert calls["sample"] > 0
    if not np.all(np.isfinite(prior_coverage)) or np.any(
        (prior_coverage < 0.0) | (prior_coverage > 1.0)
    ):
        np.testing.assert_array_equal(output, beauty)
        np.testing.assert_array_equal(state.history_matte, np.zeros((1, 1), dtype=np.float32))
        assert diagnostics.effect_matte_pixels == 0


@pytest.mark.parametrize("height,width", [(1, 1), (3, 5)])
@pytest.mark.parametrize("mode", list(FeedbackMode))
@pytest.mark.parametrize("history_source", list(HistorySource))
@pytest.mark.parametrize("fallback", list(InvalidHistoryFallback))
@pytest.mark.parametrize("persistence,refresh_probability", [(0.0, 0.0), (1.0, 1.0)])
def test_empty_effect_fast_path_is_exactly_equal_to_retained_slow_path(
    height: int,
    width: int,
    mode: FeedbackMode,
    history_source: HistorySource,
    fallback: InvalidHistoryFallback,
    persistence: float,
    refresh_probability: float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rng = np.random.default_rng(77)
    beauty = rng.random((height, width, 4)).astype(np.float32)
    motion = rng.random((height, width, 4)).astype(np.float32)
    empty = np.zeros((height, width), dtype=np.float32)
    previous = FeedbackState(
        rng.random((height, width, 4)).astype(np.float32),
        empty.copy() if mode is FeedbackMode.TRAIL else np.ones_like(empty),
        frame_number=8,
    )
    settings = FeedbackSettings(
        mode=mode,
        history_source=history_source,
        invalid_history_fallback=fallback,
        persistence=persistence,
        trail_decay=1.0,
        trail_motion_mix=0.5,
        block_size=2,
        diffusion=0.5,
        refresh_probability=refresh_probability,
        seed=77,
    )

    optimized = feedback.process_frame_with_diagnostics(
        beauty, motion, empty, previous, 9, settings
    )
    monkeypatch.setattr(feedback, "_can_skip_empty_effect_work", lambda *_args: False)
    baseline = feedback.process_frame_with_diagnostics(beauty, motion, empty, previous, 9, settings)

    for optimized_array, baseline_array in (
        (optimized[0], baseline[0]),
        (optimized[1].history, baseline[1].history),
        (optimized[1].history_matte, baseline[1].history_matte),
    ):
        np.testing.assert_array_equal(optimized_array, baseline_array)
    assert optimized[1].frame_number == baseline[1].frame_number
    assert optimized[2] == baseline[2]


@pytest.mark.parametrize("frame_count", [30, 60])
@pytest.mark.parametrize("mode", list(FeedbackMode))
def test_empty_preroll_and_recursive_entry_are_exactly_equal_to_retained_slow_path(
    frame_count: int, mode: FeedbackMode, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = FeedbackSettings(
        mode=mode,
        history_source=HistorySource.FULL_FRAME,
        invalid_history_fallback=InvalidHistoryFallback.SAME_PIXEL_HISTORY,
        persistence=1.0,
        trail_decay=1.0,
        block_size=1,
        diffusion=0.25,
        refresh_probability=0.5,
        seed=77,
    )
    empty = np.zeros((1, 1), dtype=np.float32)

    def run_sequence() -> tuple[np.ndarray, FeedbackState, object]:
        state = None
        result = None
        for frame_number in range(1, frame_count + 1):
            beauty = _rgba(1, 1, frame_number / 100.0)
            result = feedback.process_frame_with_diagnostics(
                beauty, _motion(1, 1), empty, state, frame_number, settings
            )
            state = result[1]
        result = feedback.process_frame_with_diagnostics(
            _rgba(1, 1, 0.0),
            _motion(1, 1),
            np.ones((1, 1), dtype=np.float32),
            state,
            frame_count + 1,
            settings,
        )
        return result

    optimized = run_sequence()
    monkeypatch.setattr(feedback, "_can_skip_empty_effect_work", lambda *_args: False)
    baseline = run_sequence()
    np.testing.assert_array_equal(optimized[0], baseline[0])
    np.testing.assert_array_equal(optimized[1].history, baseline[1].history)
    np.testing.assert_array_equal(optimized[1].history_matte, baseline[1].history_matte)
    assert optimized[1].frame_number == baseline[1].frame_number
    assert optimized[2] == baseline[2]


@pytest.mark.parametrize("frame_count", [30, 60])
@pytest.mark.parametrize("mode", list(FeedbackMode))
def test_full_frame_empty_preroll_keeps_latest_clean_history_for_target_entry(
    frame_count: int, mode: FeedbackMode
) -> None:
    settings = FeedbackSettings(
        mode=mode,
        history_source=HistorySource.FULL_FRAME,
        persistence=1.0,
        trail_decay=1.0,
        block_size=1,
    )
    empty = np.zeros((1, 1), dtype=np.float32)
    state = None
    latest = _rgba(1, 1, 0.0)
    for frame_number in range(1, frame_count + 1):
        latest = _rgba(1, 1, frame_number / 100.0)
        _output, state = process_frame(
            latest,
            _motion(1, 1),
            empty,
            state,
            frame_number,
            settings,
        )

    entering = _rgba(1, 1, 0.0)
    output, next_state = process_frame(
        entering,
        _motion(1, 1),
        np.ones((1, 1), dtype=np.float32),
        state,
        frame_count + 1,
        settings,
    )

    np.testing.assert_array_equal(output, latest)
    np.testing.assert_array_equal(next_state.history, latest)
    np.testing.assert_array_equal(next_state.history_matte, np.ones((1, 1), dtype=np.float32))


def test_identity_motion_applies_persistence_inside_current_matte() -> None:
    beauty = _rgba(2, 2, 0.0)
    previous = FeedbackState(
        history=_rgba(2, 2, 1.0),
        history_matte=np.ones((2, 2), dtype=np.float32),
        frame_number=1,
    )

    output, state = process_frame(
        beauty=beauty,
        motion=_motion(2, 2),
        matte=np.ones((2, 2), dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(persistence=0.75, block_size=1),
    )

    np.testing.assert_allclose(output, _rgba(2, 2, 0.75))
    np.testing.assert_array_equal(state.history, output)


def test_history_without_selected_object_coverage_is_rejected() -> None:
    beauty = _rgba(1, 2, 0.25)
    previous = FeedbackState(
        history=_rgba(1, 2, 1.0),
        history_matte=np.zeros((1, 2), dtype=np.float32),
        frame_number=1,
    )

    output, _state = process_frame(
        beauty=beauty,
        motion=_motion(1, 2),
        matte=np.ones((1, 2), dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(persistence=1.0, block_size=1),
    )

    np.testing.assert_array_equal(output, beauty)


def test_full_frame_history_reveals_offscreen_preroll_color_on_target_entrance() -> None:
    red = np.array([1.0, 0.0, 0.0, 1.0], dtype=np.float32)
    blue = np.array([0.0, 0.0, 1.0, 1.0], dtype=np.float32)
    preroll = np.broadcast_to(red, (1, 1, 4)).copy()
    entering = np.broadcast_to(blue, (1, 1, 4)).copy()
    zero_matte = np.zeros((1, 1), dtype=np.float32)
    target_matte = np.ones((1, 1), dtype=np.float32)

    _preroll_output, full_state = process_frame(
        preroll,
        _motion(1, 1),
        zero_matte,
        None,
        frame_number=1,
        settings=FeedbackSettings(history_source=HistorySource.FULL_FRAME),
    )
    full_output, _ = process_frame(
        entering,
        _motion(1, 1),
        target_matte,
        full_state,
        frame_number=2,
        settings=FeedbackSettings(
            history_source=HistorySource.FULL_FRAME,
            persistence=1.0,
            block_size=1,
        ),
    )
    _target_preroll, target_state = process_frame(
        preroll,
        _motion(1, 1),
        zero_matte,
        None,
        frame_number=1,
        settings=FeedbackSettings(),
    )
    target_output, _ = process_frame(
        entering,
        _motion(1, 1),
        target_matte,
        target_state,
        frame_number=2,
        settings=FeedbackSettings(persistence=1.0, block_size=1),
    )

    np.testing.assert_array_equal(full_output[0, 0], red)
    np.testing.assert_array_equal(target_output[0, 0], blue)


def test_full_frame_history_is_recursive_processed_output() -> None:
    first = _rgba(1, 1, 1.0)
    second = _rgba(1, 1, 0.0)
    third = _rgba(1, 1, 0.0)
    matte = np.ones((1, 1), dtype=np.float32)
    settings = FeedbackSettings(
        history_source=HistorySource.FULL_FRAME,
        persistence=0.5,
        block_size=1,
    )

    _first_output, state = process_frame(
        first, _motion(1, 1), matte, None, frame_number=1, settings=settings
    )
    second_output, state = process_frame(
        second, _motion(1, 1), matte, state, frame_number=2, settings=settings
    )
    third_output, state = process_frame(
        third, _motion(1, 1), matte, state, frame_number=3, settings=settings
    )

    np.testing.assert_array_equal(second_output, _rgba(1, 1, 0.5))
    np.testing.assert_array_equal(third_output, _rgba(1, 1, 0.25))
    np.testing.assert_array_equal(state.history, third_output)
    np.testing.assert_array_equal(state.history_matte, matte)


@pytest.mark.parametrize("invalid_value", [np.nan, np.inf, -np.inf])
def test_full_frame_rejects_nonfinite_sample_and_preserves_clean_outside_matte(
    invalid_value: float,
) -> None:
    beauty = _rgba(1, 2, 0.25)
    history = _rgba(1, 2, 1.0)
    history[0, 0, 0] = invalid_value
    previous = FeedbackState(history, np.zeros((1, 2), dtype=np.float32), frame_number=1)
    matte = np.array([[1.0, 0.0]], dtype=np.float32)

    output, _ = process_frame(
        beauty,
        _motion(1, 2),
        matte,
        previous,
        frame_number=2,
        settings=FeedbackSettings(
            history_source=HistorySource.FULL_FRAME,
            persistence=1.0,
            block_size=1,
        ),
    )

    np.testing.assert_array_equal(output, beauty)


@pytest.mark.parametrize(
    ("fallback", "expected_target"),
    [
        (
            InvalidHistoryFallback.CURRENT_BEAUTY,
            np.array(
                [
                    [[0.0, 0.0, 1.0, 1.0], [1.0, 0.0, 0.0, 1.0]],
                    [[0.0, 0.0, 1.0, 1.0], [1.0, 0.0, 0.0, 1.0]],
                ],
                dtype=np.float32,
            ),
        ),
        (
            InvalidHistoryFallback.SAME_PIXEL_HISTORY,
            np.array(
                [
                    [[1.0, 0.0, 0.0, 1.0], [1.0, 0.0, 0.0, 1.0]],
                    [[1.0, 0.0, 0.0, 1.0], [1.0, 0.0, 0.0, 1.0]],
                ],
                dtype=np.float32,
            ),
        ),
    ],
)
def test_full_frame_entering_target_uses_selected_invalid_history_fallback(
    fallback: InvalidHistoryFallback,
    expected_target: np.ndarray,
) -> None:
    red = np.array([1.0, 0.0, 0.0, 1.0], dtype=np.float32)
    blue = np.array([0.0, 0.0, 1.0, 1.0], dtype=np.float32)
    previous_beauty = np.broadcast_to(red, (2, 3, 4)).copy()
    zero_matte = np.zeros((2, 3), dtype=np.float32)
    settings = FeedbackSettings(
        mode=FeedbackMode.HARD_LOCALIZED,
        history_source=HistorySource.FULL_FRAME,
        invalid_history_fallback=fallback,
        persistence=1.0,
        block_size=1,
        motion_quantization=0.0,
        diffusion=0.0,
        refresh_probability=0.0,
    )
    _, previous = process_frame(
        previous_beauty,
        _motion(2, 3),
        zero_matte,
        previous_state=None,
        frame_number=1,
        settings=settings,
    )
    current_beauty = np.broadcast_to(blue, (2, 3, 4)).copy()
    entering_matte = np.zeros((2, 3), dtype=np.float32)
    entering_matte[:, :2] = 1.0
    entering_motion = _motion(2, 3)
    entering_motion[:, :2, 0] = 1.0

    output, _ = process_frame(
        current_beauty,
        entering_motion,
        entering_matte,
        previous,
        frame_number=2,
        settings=settings,
    )

    np.testing.assert_array_equal(output[:, :2], expected_target)
    np.testing.assert_array_equal(output[:, 2], current_beauty[:, 2])


def test_clean_full_frame_trail_samples_only_primary_history_and_trail_mask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = _rgba(2, 3, 1.0)
    history_matte = np.array([[0.0, 0.5, 1.0], [1.0, 0.5, 0.0]], dtype=np.float32)
    previous = FeedbackState(history, history_matte, frame_number=1)
    sampled_images: list[np.ndarray] = []
    real_sample = feedback.sample_with_plan

    def recording_sample(image: np.ndarray, plan: object) -> tuple[np.ndarray, np.ndarray]:
        sampled_images.append(image)
        return real_sample(image, plan)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(feedback, "sample_with_plan", recording_sample)

    output, state = process_frame(
        beauty=_rgba(2, 3, 0.0),
        motion=_motion(2, 3),
        matte=np.ones((2, 3), dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(
            mode=FeedbackMode.TRAIL,
            history_source=HistorySource.FULL_FRAME,
            invalid_history_fallback=InvalidHistoryFallback.SAME_PIXEL_HISTORY,
            persistence=1.0,
            trail_decay=1.0,
            block_size=1,
        ),
    )

    assert len(sampled_images) == 2
    assert sampled_images[0] is history
    assert sampled_images[1] is history_matte
    np.testing.assert_array_equal(output, history)
    np.testing.assert_array_equal(state.history_matte, np.ones((2, 3), dtype=np.float32))


def test_full_frame_same_pixel_fallback_does_not_identity_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = _rgba(1, 2, 1.0)
    previous = FeedbackState(history, np.zeros((1, 2), dtype=np.float32), frame_number=1)
    sampled_validity: list[np.ndarray] = []
    real_sample = feedback.sample_with_plan

    def recording_sample(image: np.ndarray, plan: object) -> tuple[np.ndarray, np.ndarray]:
        result = real_sample(image, plan)  # ty: ignore[invalid-argument-type]
        sampled_validity.append(result[1])
        return result

    monkeypatch.setattr(feedback, "sample_with_plan", recording_sample)
    motion = _motion(1, 2)
    motion[0, 0, 0] = 1.0

    output, _state = process_frame(
        beauty=_rgba(1, 2, 0.0),
        motion=motion,
        matte=np.array([[1.0, 0.0]], dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(
            history_source=HistorySource.FULL_FRAME,
            invalid_history_fallback=InvalidHistoryFallback.SAME_PIXEL_HISTORY,
            persistence=1.0,
            block_size=1,
            motion_quantization=0.0,
        ),
    )

    assert len(sampled_validity) == 1
    assert not sampled_validity[0][0, 0]
    np.testing.assert_array_equal(output[0, 0], history[0, 0])


def test_full_frame_same_pixel_fallback_replaces_only_invalid_warp_samples() -> None:
    red = np.array([1.0, 0.0, 0.0, 1.0], dtype=np.float32)
    green = np.array([0.0, 1.0, 0.0, 1.0], dtype=np.float32)
    blue = np.array([0.0, 0.0, 1.0, 1.0], dtype=np.float32)
    history = np.broadcast_to(red, (1, 3, 4)).copy()
    history[0, 0] = green
    previous = FeedbackState(history, np.zeros((1, 3), dtype=np.float32), frame_number=1)
    beauty = np.broadcast_to(blue, (1, 3, 4)).copy()
    motion = _motion(1, 3)
    motion[0, :2, 0] = 1.0
    matte = np.array([[1.0, 1.0, 0.0]], dtype=np.float32)

    output, _ = process_frame(
        beauty,
        motion,
        matte,
        previous,
        frame_number=2,
        settings=FeedbackSettings(
            history_source=HistorySource.FULL_FRAME,
            invalid_history_fallback=InvalidHistoryFallback.SAME_PIXEL_HISTORY,
            persistence=1.0,
            block_size=1,
            motion_quantization=0.0,
        ),
    )

    np.testing.assert_array_equal(output[0, 0], green)
    np.testing.assert_array_equal(output[0, 1], green)
    np.testing.assert_array_equal(output[0, 2], blue)


@pytest.mark.parametrize("invalid_value", [np.nan, np.inf, -np.inf])
def test_full_frame_same_pixel_fallback_rejects_nonfinite_history(
    invalid_value: float,
) -> None:
    history = _rgba(1, 2, 1.0)
    history[0, 0, 0] = invalid_value
    previous = FeedbackState(history, np.zeros((1, 2), dtype=np.float32), frame_number=1)
    beauty = _rgba(1, 2, 0.25)
    motion = _motion(1, 2)
    motion[0, 0, 0] = 1.0

    output, _ = process_frame(
        beauty,
        motion,
        np.array([[1.0, 0.0]], dtype=np.float32),
        previous,
        frame_number=2,
        settings=FeedbackSettings(
            history_source=HistorySource.FULL_FRAME,
            invalid_history_fallback=InvalidHistoryFallback.SAME_PIXEL_HISTORY,
            persistence=1.0,
            block_size=1,
            motion_quantization=0.0,
        ),
    )

    np.testing.assert_array_equal(output, beauty)


def test_full_frame_same_pixel_fallback_replaces_contaminated_primary_sample() -> None:
    history = _rgba(1, 3, 1.0)
    history[0, 0, 0] = np.nan
    history[0, 1] = np.array([0.0, 1.0, 0.0, 1.0], dtype=np.float32)
    previous = FeedbackState(history, np.zeros((1, 3), dtype=np.float32), frame_number=1)
    beauty = _rgba(1, 3, 0.25)
    motion = _motion(1, 3)
    motion[0, 1, 0] = 0.5

    output, _ = process_frame(
        beauty,
        motion,
        np.array([[0.0, 1.0, 0.0]], dtype=np.float32),
        previous,
        frame_number=2,
        settings=FeedbackSettings(
            history_source=HistorySource.FULL_FRAME,
            invalid_history_fallback=InvalidHistoryFallback.SAME_PIXEL_HISTORY,
            persistence=1.0,
            block_size=1,
            motion_quantization=0.0,
        ),
    )

    np.testing.assert_array_equal(output[0, 1], history[0, 1])
    np.testing.assert_array_equal(output[0, [0, 2]], beauty[0, [0, 2]])


def test_full_frame_out_of_bounds_sample_falls_back_without_wrapping() -> None:
    beauty = _rgba(1, 2, 0.25)
    history = _rgba(1, 2, 0.0)
    history[0, 1] = 1.0
    previous = FeedbackState(history, np.zeros((1, 2), dtype=np.float32), frame_number=1)
    motion = _motion(1, 2)
    motion[0, 0, 0] = 1.0

    output, _ = process_frame(
        beauty,
        motion,
        np.array([[1.0, 0.0]], dtype=np.float32),
        previous,
        frame_number=2,
        settings=FeedbackSettings(
            history_source=HistorySource.FULL_FRAME,
            persistence=1.0,
            block_size=1,
            motion_quantization=0.0,
        ),
    )

    np.testing.assert_array_equal(output, beauty)


def test_rg_motion_samples_history_opposite_the_forward_displacement() -> None:
    beauty = _rgba(1, 3, 0.0)
    history = _rgba(1, 3, 0.0)
    history[0, 0] = 1.0
    previous_matte = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
    previous = FeedbackState(history, previous_matte, frame_number=1)
    motion = _motion(1, 3)
    motion[0, 1, 0] = 1.0
    current_matte = np.array([[0.0, 1.0, 0.0]], dtype=np.float32)

    output, _state = process_frame(
        beauty=beauty,
        motion=motion,
        matte=current_matte,
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(persistence=1.0, block_size=1),
    )

    np.testing.assert_array_equal(output[0, 1], np.ones(4, dtype=np.float32))
    np.testing.assert_array_equal(output[:, (0, 2)], beauty[:, (0, 2)])


@pytest.mark.parametrize(
    ("vector", "settings", "expected"),
    [
        (
            (9.0, 9.0, 1.0, 0.0),
            FeedbackSettings(
                persistence=1.0,
                block_size=1,
                motion_channels=MotionChannels.BA,
                motion_quantization=0.0,
            ),
            32.0,
        ),
        (
            (-1.0, 0.0, 0.0, 0.0),
            FeedbackSettings(
                persistence=1.0,
                block_size=1,
                reverse_motion=True,
                motion_quantization=0.0,
            ),
            32.0,
        ),
        (
            (-1.0, 0.0, 0.0, 0.0),
            FeedbackSettings(
                persistence=1.0,
                block_size=1,
                flip_x=True,
                motion_quantization=0.0,
            ),
            32.0,
        ),
        (
            (0.0, -1.0, 0.0, 0.0),
            FeedbackSettings(
                persistence=1.0,
                block_size=1,
                flip_y=True,
                motion_quantization=0.0,
            ),
            23.0,
        ),
        (
            (0.5, 0.0, 0.0, 0.0),
            FeedbackSettings(
                persistence=1.0,
                block_size=1,
                motion_gain=2.0,
                motion_quantization=0.0,
            ),
            32.0,
        ),
        (
            (3.0, 0.0, 0.0, 0.0),
            FeedbackSettings(
                persistence=1.0,
                block_size=1,
                motion_clamp=1.0,
                motion_quantization=0.0,
            ),
            32.0,
        ),
        (
            (0.6, 0.0, 0.0, 0.0),
            FeedbackSettings(
                persistence=1.0,
                block_size=1,
                motion_quantization=1.0,
            ),
            32.0,
        ),
    ],
)
def test_motion_decode_controls_choose_the_documented_source_pixel(
    vector: tuple[float, float, float, float],
    settings: FeedbackSettings,
    expected: float,
) -> None:
    scalar = np.fromfunction(lambda y, x: y * 10 + x, (7, 7), dtype=np.float32).astype(np.float32)
    history = np.repeat(scalar[..., None], 4, axis=2)
    previous = FeedbackState(history, np.ones((7, 7), dtype=np.float32), frame_number=1)
    motion = _motion(7, 7)
    motion[3, 3] = vector
    matte = np.zeros((7, 7), dtype=np.float32)
    matte[3, 3] = 1.0
    output, _state = process_frame(
        beauty=_rgba(7, 7, 0.0),
        motion=motion,
        matte=matte,
        previous_state=previous,
        frame_number=2,
        settings=settings,
    )

    np.testing.assert_allclose(output[3, 3], np.full(4, expected, dtype=np.float32))


def test_motion_gain_and_clamp_are_overflow_safe() -> None:
    beauty = _rgba(1, 3, 0.0)
    history = _rgba(1, 3, 0.0)
    history[0, 0] = 1.0
    previous = FeedbackState(history, np.ones((1, 3), dtype=np.float32), frame_number=1)
    motion = _motion(1, 3)
    motion[0, 1, 0] = np.finfo(np.float32).max

    output, _state = process_frame(
        beauty=beauty,
        motion=motion,
        matte=np.array([[0.0, 1.0, 0.0]], dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(
            persistence=1.0,
            block_size=1,
            motion_gain=1e300,
            motion_clamp=1.0,
            motion_quantization=0.0,
        ),
    )

    np.testing.assert_array_equal(output[0, 1], np.ones(4, dtype=np.float32))


def test_block_motion_uses_matte_weighted_representative_and_expands_to_edge_blocks() -> None:
    beauty = _rgba(1, 3, 0.0)
    history = _rgba(1, 3, 0.0)
    history[0, 0] = 1.0
    previous = FeedbackState(history, np.ones((1, 3), dtype=np.float32), frame_number=1)
    motion = _motion(1, 3)
    motion[0, 0, 0] = 0.0
    motion[0, 1, 0] = 2.0
    matte = np.array([[1.0, 1.0, 0.0]], dtype=np.float32)

    output, _state = process_frame(
        beauty=beauty,
        motion=motion,
        matte=matte,
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(
            persistence=1.0,
            block_size=2,
            motion_quantization=0.0,
        ),
    )

    np.testing.assert_array_equal(output[0, 1], np.ones(4, dtype=np.float32))
    np.testing.assert_array_equal(output[0, 2], beauty[0, 2])


def test_numpy_integer_seed_and_frame_are_supported() -> None:
    previous = FeedbackState(_rgba(2, 2, 1.0), np.ones((2, 2), dtype=np.float32), frame_number=1)

    output, state = process_frame(
        beauty=_rgba(2, 2, 0.0),
        motion=_motion(2, 2),
        matte=np.ones((2, 2), dtype=np.float32),
        previous_state=previous,
        frame_number=np.int64(2),  # ty: ignore[invalid-argument-type]
        settings=FeedbackSettings(
            persistence=1.0,
            block_size=1,
            diffusion=0.25,
            refresh_probability=0.5,
            seed=np.int64(7),  # ty: ignore[invalid-argument-type]
        ),
    )

    assert output.shape == (2, 2, 4)
    assert state.frame_number == 2


def test_diffusion_is_deterministic_from_seed_frame_and_block_coordinates() -> None:
    scalar = np.fromfunction(lambda y, x: y * 10 + x, (8, 8), dtype=np.float32).astype(np.float32)
    history = np.repeat(scalar[..., None], 4, axis=2)
    previous = FeedbackState(history, np.ones((8, 8), dtype=np.float32), frame_number=3)

    def process(settings: FeedbackSettings, frame_number: int = 4) -> np.ndarray:
        output, _state = process_frame(
            beauty=_rgba(8, 8, 0.0),
            motion=_motion(8, 8),
            matte=np.ones((8, 8), dtype=np.float32),
            previous_state=previous,
            frame_number=frame_number,
            settings=settings,
        )
        return output

    first = process(
        FeedbackSettings(
            persistence=1.0,
            block_size=2,
            motion_quantization=0.0,
            diffusion=0.4,
            seed=17,
        )
    )
    repeated = process(
        FeedbackSettings(
            persistence=1.0,
            block_size=2,
            motion_quantization=0.0,
            diffusion=0.4,
            seed=17,
        )
    )
    other_seed = process(
        FeedbackSettings(
            persistence=1.0,
            block_size=2,
            motion_quantization=0.0,
            diffusion=0.4,
            seed=18,
        )
    )
    other_frame = process(
        FeedbackSettings(
            persistence=1.0,
            block_size=2,
            motion_quantization=0.0,
            diffusion=0.4,
            seed=17,
        ),
        frame_number=5,
    )

    np.testing.assert_array_equal(first, repeated)
    assert not np.array_equal(first, other_seed)
    assert not np.array_equal(first, other_frame)
    assert not np.array_equal(first[2:4, 2:4], first[2:4, 4:6])


@pytest.mark.parametrize("shape", [(0, 2, 4), (2, 0, 4)])
def test_process_frame_rejects_empty_frame_dimensions(shape: tuple[int, int, int]) -> None:
    height, width, _channels = shape

    with pytest.raises(ValueError, match="frame dimensions must be nonzero"):
        process_frame(
            beauty=np.empty(shape, dtype=np.float32),
            motion=np.empty(shape, dtype=np.float32),
            matte=np.empty((height, width), dtype=np.float32),
            previous_state=None,
            frame_number=1,
            settings=FeedbackSettings(),
        )


def test_process_frame_requires_float32_motion() -> None:
    with pytest.raises(TypeError, match="motion must use float32"):
        process_frame(
            beauty=_rgba(2, 2, 0.0),
            motion=np.zeros((2, 2, 4), dtype=np.float64),
            matte=np.ones((2, 2), dtype=np.float32),
            previous_state=None,
            frame_number=1,
            settings=FeedbackSettings(),
        )


@pytest.mark.parametrize(
    ("argument", "value", "message"),
    [
        ("frame_number", 1.5, "frame_number must be an integer"),
        ("frame_number", True, "frame_number must be an integer"),
        (
            "previous_state",
            object(),
            "previous_state must be a FeedbackState value or None",
        ),
        ("settings", object(), "settings must be a FeedbackSettings value"),
        ("force_reset", 1, "force_reset must be a boolean"),
    ],
)
def test_process_frame_validates_scalar_contracts(
    argument: str, value: object, message: str
) -> None:
    arguments: dict[str, object] = {
        "beauty": _rgba(1, 1, 0.0),
        "motion": _motion(1, 1),
        "matte": np.ones((1, 1), dtype=np.float32),
        "previous_state": None,
        "frame_number": 1,
        "settings": FeedbackSettings(),
        "force_reset": False,
    }
    arguments[argument] = value

    with pytest.raises(TypeError, match=message):
        process_frame(**arguments)  # type: ignore[arg-type]


def test_forced_reset_discards_available_history() -> None:
    beauty = _rgba(2, 2, 0.2)
    previous = FeedbackState(_rgba(2, 2, 1.0), np.ones((2, 2), dtype=np.float32), frame_number=1)

    output, state = process_frame(
        beauty=beauty,
        motion=_motion(2, 2),
        matte=np.ones((2, 2), dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(
            history_source=HistorySource.FULL_FRAME,
            persistence=1.0,
        ),
        force_reset=True,
    )

    np.testing.assert_array_equal(output, beauty)
    np.testing.assert_array_equal(state.history, beauty)
    np.testing.assert_array_equal(state.history_matte, np.ones((2, 2), dtype=np.float32))


def test_hard_localization_preserves_nonzero_clean_beauty_outside_current_matte() -> None:
    beauty = _rgba(2, 3, 0.25)
    previous = FeedbackState(_rgba(2, 3, 3.0), np.ones((2, 3), dtype=np.float32), frame_number=1)
    matte = np.array([[0.0, 1.0, 0.0], [0.0, 0.0, 0.0]], dtype=np.float32)

    output, _state = process_frame(
        beauty=beauty,
        motion=_motion(2, 3),
        matte=matte,
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(persistence=1.0, block_size=1),
    )

    outside = matte == 0.0
    np.testing.assert_array_equal(output[outside], beauty[outside])


def test_extreme_preset_leaves_a_material_deterministic_screen_space_trail() -> None:
    settings = extreme_full_frame_feedback_settings()
    height, width = 5, 9
    first_beauty = _rgba(height, width, 0.0)
    first_beauty[2, 1] = np.ones(4, dtype=np.float32)
    second_beauty = _rgba(height, width, 0.0)
    first_matte = np.zeros((height, width), dtype=np.float32)
    first_matte[2, 1] = 1.0
    second_matte = np.zeros((height, width), dtype=np.float32)
    second_matte[2, 5] = 1.0
    motion = _motion(height, width)
    motion[2, 5, 0] = 4.0

    def run() -> tuple[np.ndarray, np.ndarray]:
        _first, state = process_frame(
            first_beauty, motion, first_matte, None, frame_number=1, settings=settings
        )
        second, state = process_frame(
            second_beauty, motion, second_matte, state, frame_number=2, settings=settings
        )
        return second, state.history_matte

    first_output, first_coverage = run()
    second_output, second_coverage = run()

    assert settings.history_source is HistorySource.FULL_FRAME
    assert settings.mode is FeedbackMode.TRAIL
    assert settings.invalid_history_fallback is InvalidHistoryFallback.SAME_PIXEL_HISTORY
    assert settings.persistence == 1.0
    assert settings.trail_decay == 0.995
    assert settings.trail_motion_mix == 0.1
    assert settings.refresh_probability == 0.0
    assert settings.block_size == 32
    assert settings.motion_quantization == 8.0
    assert settings.diffusion == 6.0
    assert first_coverage[2, 1] > 0.85
    assert first_output[2, 1, 0] > 0.85
    np.testing.assert_array_equal(second_output, first_output)
    np.testing.assert_array_equal(second_coverage, first_coverage)


@pytest.mark.parametrize(
    ("trail_motion_mix", "expected_coverage"),
    [
        (0.0, np.array([[0.8, 1.0, 0.0]], dtype=np.float32)),
        (0.25, np.array([[0.6, 1.0, 0.0]], dtype=np.float32)),
        (1.0, np.array([[0.0, 1.0, 0.0]], dtype=np.float32)),
    ],
)
def test_full_frame_trail_mix_blends_screen_and_motion_following_coverage(
    trail_motion_mix: float,
    expected_coverage: np.ndarray,
) -> None:
    previous = FeedbackState(
        _rgba(1, 3, 1.0),
        np.array([[0.8, 0.0, 0.0]], dtype=np.float32),
        frame_number=1,
    )
    motion = _motion(1, 3)
    motion[..., 0] = 1.0

    _output, state = process_frame(
        beauty=_rgba(1, 3, 0.0),
        motion=motion,
        matte=np.array([[0.0, 1.0, 0.0]], dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(
            mode=FeedbackMode.TRAIL,
            history_source=HistorySource.FULL_FRAME,
            trail_decay=1.0,
            trail_motion_mix=trail_motion_mix,
            persistence=1.0,
            block_size=3,
            motion_quantization=0.0,
        ),
    )

    np.testing.assert_allclose(state.history_matte, expected_coverage, atol=1e-7)


def test_full_frame_trail_uses_effect_mask_for_temporal_coverage_only() -> None:
    beauty = _rgba(1, 2, 0.0)
    history = _rgba(1, 2, 0.0)
    history[0, 0] = np.array([1.0, 0.0, 0.0, 1.0], dtype=np.float32)
    history[0, 1] = np.array([0.0, 0.0, 1.0, 1.0], dtype=np.float32)
    previous = FeedbackState(
        history,
        np.array([[1.0, 0.0]], dtype=np.float32),
        frame_number=1,
    )

    output, state = process_frame(
        beauty=beauty,
        motion=_motion(1, 2),
        matte=np.array([[0.0, 1.0]], dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(
            mode=FeedbackMode.TRAIL,
            history_source=HistorySource.FULL_FRAME,
            trail_decay=0.5,
            persistence=1.0,
            block_size=1,
        ),
    )

    np.testing.assert_array_equal(output[0, 0], np.array([0.5, 0.0, 0.0, 0.5], dtype=np.float32))
    np.testing.assert_array_equal(output[0, 1], np.array([0.0, 0.0, 1.0, 1.0], dtype=np.float32))
    np.testing.assert_array_equal(state.history_matte, np.array([[0.5, 1.0]], dtype=np.float32))
    np.testing.assert_array_equal(state.history, output)


@pytest.mark.parametrize(
    ("current_coverage", "expected_coverage"),
    [(0.4, 0.6), (0.9, 0.9)],
)
def test_full_frame_trail_uses_clamped_max_union_for_current_reinforcement(
    current_coverage: float,
    expected_coverage: float,
) -> None:
    previous = FeedbackState(
        _rgba(1, 1, 1.0),
        np.full((1, 1), 0.8, dtype=np.float32),
        frame_number=1,
    )

    output, state = process_frame(
        _rgba(1, 1, 0.0),
        _motion(1, 1),
        np.full((1, 1), current_coverage, dtype=np.float32),
        previous,
        frame_number=2,
        settings=FeedbackSettings(
            mode=FeedbackMode.TRAIL,
            history_source=HistorySource.FULL_FRAME,
            trail_decay=0.75,
            persistence=1.0,
            block_size=1,
        ),
    )

    np.testing.assert_allclose(output, _rgba(1, 1, expected_coverage), atol=1e-7)
    np.testing.assert_allclose(
        state.history_matte,
        np.full((1, 1), expected_coverage, dtype=np.float32),
        atol=1e-7,
    )


def test_full_frame_trail_recurses_and_decays_without_current_reinforcement() -> None:
    settings = FeedbackSettings(
        mode=FeedbackMode.TRAIL,
        history_source=HistorySource.FULL_FRAME,
        trail_decay=0.5,
        persistence=1.0,
        block_size=1,
    )
    beauty = _rgba(1, 1, 0.0)
    first = _rgba(1, 1, 1.0)

    _output, state = process_frame(
        first,
        _motion(1, 1),
        np.ones((1, 1), dtype=np.float32),
        None,
        frame_number=1,
        settings=settings,
    )
    second, state = process_frame(
        beauty,
        _motion(1, 1),
        np.zeros((1, 1), dtype=np.float32),
        state,
        frame_number=2,
        settings=settings,
    )
    third, state = process_frame(
        beauty,
        _motion(1, 1),
        np.zeros((1, 1), dtype=np.float32),
        state,
        frame_number=3,
        settings=settings,
    )

    np.testing.assert_array_equal(second, _rgba(1, 1, 0.5))
    np.testing.assert_array_equal(third, _rgba(1, 1, 0.125))
    np.testing.assert_array_equal(state.history, third)
    np.testing.assert_array_equal(state.history_matte, np.full((1, 1), 0.25, dtype=np.float32))


@pytest.mark.parametrize(
    ("trail_decay", "expected_output", "expected_coverage"),
    [(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)],
)
def test_full_frame_trail_decay_controls_old_effect_coverage(
    trail_decay: float,
    expected_output: float,
    expected_coverage: float,
) -> None:
    previous = FeedbackState(
        _rgba(1, 1, 1.0),
        np.ones((1, 1), dtype=np.float32),
        frame_number=1,
    )

    output, state = process_frame(
        _rgba(1, 1, 0.0),
        _motion(1, 1),
        np.zeros((1, 1), dtype=np.float32),
        previous,
        frame_number=2,
        settings=FeedbackSettings(
            mode=FeedbackMode.TRAIL,
            history_source=HistorySource.FULL_FRAME,
            trail_decay=trail_decay,
            persistence=1.0,
            block_size=1,
        ),
    )

    np.testing.assert_array_equal(output, _rgba(1, 1, expected_output))
    np.testing.assert_array_equal(
        state.history_matte,
        np.full((1, 1), expected_coverage, dtype=np.float32),
    )


def test_full_frame_trail_refresh_falls_back_to_current_beauty() -> None:
    beauty = _rgba(3, 5, 0.25)
    previous = FeedbackState(
        _rgba(3, 5, 1.0),
        np.ones((3, 5), dtype=np.float32),
        frame_number=1,
    )

    output, state = process_frame(
        beauty,
        _motion(3, 5),
        np.zeros((3, 5), dtype=np.float32),
        previous,
        frame_number=2,
        settings=FeedbackSettings(
            mode=FeedbackMode.TRAIL,
            history_source=HistorySource.FULL_FRAME,
            trail_decay=0.5,
            persistence=1.0,
            block_size=2,
            refresh_probability=1.0,
        ),
    )

    np.testing.assert_array_equal(output, beauty)
    np.testing.assert_array_equal(state.history, beauty)
    np.testing.assert_array_equal(state.history_matte, np.full((3, 5), 0.5, dtype=np.float32))


def test_full_frame_trail_invalid_color_falls_back_without_erasing_effect_coverage() -> None:
    history = _rgba(1, 1, 1.0)
    history[0, 0, 0] = np.nan
    previous = FeedbackState(
        history,
        np.ones((1, 1), dtype=np.float32),
        frame_number=1,
    )
    beauty = _rgba(1, 1, 0.25)

    output, state = process_frame(
        beauty,
        _motion(1, 1),
        np.zeros((1, 1), dtype=np.float32),
        previous,
        frame_number=2,
        settings=FeedbackSettings(
            mode=FeedbackMode.TRAIL,
            history_source=HistorySource.FULL_FRAME,
            trail_decay=0.5,
            persistence=1.0,
            block_size=1,
        ),
    )

    np.testing.assert_array_equal(output, beauty)
    np.testing.assert_array_equal(state.history_matte, np.full((1, 1), 0.5, dtype=np.float32))


@pytest.mark.parametrize("invalid_coverage", [np.nan, -0.1, 1.1])
def test_full_frame_trail_rejects_invalid_effect_mask_sample(invalid_coverage: float) -> None:
    previous = FeedbackState(
        _rgba(1, 2, 1.0),
        np.array([[invalid_coverage, 1.0]], dtype=np.float32),
        frame_number=1,
    )
    motion = _motion(1, 2)
    motion[0, 1, 0] = 0.5
    beauty = _rgba(1, 2, 0.25)

    output, state = process_frame(
        beauty,
        motion,
        np.zeros((1, 2), dtype=np.float32),
        previous,
        frame_number=2,
        settings=FeedbackSettings(
            mode=FeedbackMode.TRAIL,
            history_source=HistorySource.FULL_FRAME,
            trail_decay=1.0,
            persistence=1.0,
            block_size=1,
            motion_quantization=0.0,
        ),
    )

    np.testing.assert_array_equal(output[0, 0], beauty[0, 0])
    np.testing.assert_array_equal(output[0, 1], previous.history[0, 1])
    np.testing.assert_array_equal(state.history_matte, np.array([[0.0, 1.0]], dtype=np.float32))


def test_full_frame_trail_out_of_bounds_color_falls_back_to_current_beauty() -> None:
    previous = FeedbackState(
        _rgba(1, 1, 1.0),
        np.ones((1, 1), dtype=np.float32),
        frame_number=1,
    )
    motion = _motion(1, 1)
    motion[0, 0, 0] = 1.0
    beauty = _rgba(1, 1, 0.25)

    output, state = process_frame(
        beauty,
        motion,
        np.ones((1, 1), dtype=np.float32),
        previous,
        frame_number=2,
        settings=FeedbackSettings(
            mode=FeedbackMode.TRAIL,
            history_source=HistorySource.FULL_FRAME,
            trail_decay=1.0,
            persistence=1.0,
            block_size=1,
            motion_quantization=0.0,
        ),
    )

    np.testing.assert_array_equal(output, beauty)
    np.testing.assert_array_equal(state.history_matte, np.ones((1, 1), dtype=np.float32))


def test_full_frame_trail_supports_odd_dimensions_and_partial_edge_blocks() -> None:
    height, width = 3, 5
    previous = FeedbackState(
        _rgba(height, width, 1.0),
        np.ones((height, width), dtype=np.float32),
        frame_number=1,
    )

    output, state = process_frame(
        _rgba(height, width, 0.0),
        _motion(height, width),
        np.zeros((height, width), dtype=np.float32),
        previous,
        frame_number=2,
        settings=FeedbackSettings(
            mode=FeedbackMode.TRAIL,
            history_source=HistorySource.FULL_FRAME,
            trail_decay=0.5,
            persistence=1.0,
            block_size=2,
        ),
    )

    np.testing.assert_array_equal(output, _rgba(height, width, 0.5))
    np.testing.assert_array_equal(
        state.history_matte, np.full((height, width), 0.5, dtype=np.float32)
    )
    assert output.shape == (height, width, 4)
    assert output.dtype == np.float32


def test_target_only_trail_ignores_full_frame_trail_motion_mix() -> None:
    beauty = _rgba(1, 3, 0.2)
    previous = FeedbackState(
        _rgba(1, 3, 1.0),
        np.array([[1.0, 0.0, 0.0]], dtype=np.float32),
        frame_number=1,
    )
    matte = np.array([[0.0, 1.0, 0.0]], dtype=np.float32)
    motion = _motion(1, 3)
    motion[0, 1, 0] = 1.0

    results = [
        process_frame(
            beauty,
            motion,
            matte,
            previous,
            frame_number=2,
            settings=FeedbackSettings(
                mode=FeedbackMode.TRAIL,
                history_source=HistorySource.TARGET_ONLY,
                trail_motion_mix=mix,
                persistence=1.0,
                block_size=1,
            ),
        )
        for mix in (0.0, 1.0)
    ]

    np.testing.assert_array_equal(results[0][0], results[1][0])
    np.testing.assert_array_equal(results[0][1].history_matte, results[1][1].history_matte)


def test_full_frame_screen_space_trail_rejects_nonfinite_screen_mask() -> None:
    previous = FeedbackState(
        _rgba(1, 2, 1.0),
        np.array([[np.inf, 0.75]], dtype=np.float32),
        frame_number=1,
    )

    output, state = process_frame(
        _rgba(1, 2, 0.0),
        _motion(1, 2),
        np.zeros((1, 2), dtype=np.float32),
        previous,
        frame_number=2,
        settings=FeedbackSettings(
            mode=FeedbackMode.TRAIL,
            history_source=HistorySource.FULL_FRAME,
            trail_decay=1.0,
            trail_motion_mix=0.0,
            persistence=1.0,
            block_size=1,
        ),
    )

    assert np.all(np.isfinite(output))
    np.testing.assert_array_equal(state.history_matte, np.array([[0.0, 0.75]], dtype=np.float32))


def test_trail_mode_retains_decayed_selected_object_history_outside_current_matte() -> None:
    beauty = _rgba(1, 3, 0.2)
    history = _rgba(1, 3, 100.0)
    history[0, 0] = 1.0
    previous = FeedbackState(
        history,
        np.array([[1.0, 0.0, 0.0]], dtype=np.float32),
        frame_number=1,
    )
    current_matte = np.array([[0.0, 1.0, 0.0]], dtype=np.float32)
    motion = _motion(1, 3)
    motion[0, 1, 0] = 1.0

    output, state = process_frame(
        beauty=beauty,
        motion=motion,
        matte=current_matte,
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(
            mode=FeedbackMode.TRAIL,
            trail_decay=0.5,
            persistence=1.0,
            block_size=1,
        ),
    )

    np.testing.assert_allclose(output[0, 0], np.full(4, 0.6, dtype=np.float32))
    np.testing.assert_array_equal(output[0, 1], np.ones(4, dtype=np.float32))
    np.testing.assert_array_equal(output[0, 2], beauty[0, 2])
    np.testing.assert_array_equal(
        state.history_matte,
        np.array([[0.5, 1.0, 0.0]], dtype=np.float32),
    )


@pytest.mark.parametrize(
    ("trail_decay", "expected_history", "expected_coverage"),
    [(0.0, 0.2, 0.0), (1.0, 1.0, 1.0)],
)
def test_trail_decay_zero_clears_and_one_retains_selected_history(
    trail_decay: float,
    expected_history: float,
    expected_coverage: float,
) -> None:
    previous = FeedbackState(
        _rgba(1, 1, 1.0),
        np.ones((1, 1), dtype=np.float32),
        frame_number=1,
    )

    output, state = process_frame(
        beauty=_rgba(1, 1, 0.2),
        motion=_motion(1, 1),
        matte=np.zeros((1, 1), dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(
            mode=FeedbackMode.TRAIL,
            trail_decay=trail_decay,
            persistence=1.0,
            block_size=1,
        ),
    )

    np.testing.assert_allclose(output, _rgba(1, 1, expected_history))
    np.testing.assert_array_equal(
        state.history_matte,
        np.full((1, 1), expected_coverage, dtype=np.float32),
    )


def test_invalid_warped_history_cannot_create_trail_coverage() -> None:
    history = _rgba(1, 2, 1.0)
    history[0, 0, 0] = np.nan
    previous = FeedbackState(
        history,
        np.array([[1.0, 0.0]], dtype=np.float32),
        frame_number=1,
    )
    beauty = _rgba(1, 2, 0.25)

    output, state = process_frame(
        beauty=beauty,
        motion=_motion(1, 2),
        matte=np.zeros((1, 2), dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(
            mode=FeedbackMode.TRAIL,
            trail_decay=1.0,
            persistence=1.0,
            block_size=1,
        ),
    )

    np.testing.assert_array_equal(output, beauty)
    np.testing.assert_array_equal(state.history_matte, np.zeros((1, 2), dtype=np.float32))


def test_non_finite_warped_history_falls_back_to_clean_beauty() -> None:
    beauty = _rgba(1, 2, 0.25)
    history = _rgba(1, 2, 1.0)
    history[0, 0, 0] = np.nan
    previous = FeedbackState(history, np.ones((1, 2), dtype=np.float32), frame_number=1)

    output, _state = process_frame(
        beauty=beauty,
        motion=_motion(1, 2),
        matte=np.ones((1, 2), dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(persistence=1.0, block_size=1),
    )

    np.testing.assert_array_equal(output[0, 0], beauty[0, 0])
    np.testing.assert_array_equal(output[0, 1], previous.history[0, 1])


@pytest.mark.parametrize("invalid_value", [np.nan, np.inf])
def test_fractional_sample_rejects_invalid_covered_history(invalid_value: float) -> None:
    beauty = _rgba(1, 2, 0.25)
    history = _rgba(1, 2, 1.0)
    history[0, 0, 0] = invalid_value
    previous = FeedbackState(history, np.ones((1, 2), dtype=np.float32), frame_number=1)
    motion = _motion(1, 2)
    motion[0, 1, 0] = 0.5

    output, _state = process_frame(
        beauty=beauty,
        motion=motion,
        matte=np.array([[0.0, 1.0]], dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(
            persistence=1.0,
            block_size=1,
            motion_quantization=0.0,
        ),
    )

    np.testing.assert_array_equal(output[0, 1], beauty[0, 1])


def test_tiny_fractional_contribution_from_invalid_history_is_rejected() -> None:
    beauty = _rgba(1, 2, 0.25)
    history = _rgba(1, 2, 1.0)
    history[0, 0, 0] = np.nan
    previous = FeedbackState(history, np.ones((1, 2), dtype=np.float32), frame_number=1)
    motion = _motion(1, 2)
    motion[0, 1, 0] = 1e-7

    output, _state = process_frame(
        beauty=beauty,
        motion=motion,
        matte=np.array([[0.0, 1.0]], dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(
            persistence=1.0,
            block_size=1,
            motion_quantization=0.0,
        ),
    )

    np.testing.assert_array_equal(output[0, 1], beauty[0, 1])


def test_fractional_sample_rejects_invalid_history_matte() -> None:
    beauty = _rgba(1, 2, 0.25)
    previous = FeedbackState(
        _rgba(1, 2, 1.0),
        np.array([[2.0, 1.0]], dtype=np.float32),
        frame_number=1,
    )
    motion = _motion(1, 2)
    motion[0, 1, 0] = 0.5

    output, _state = process_frame(
        beauty=beauty,
        motion=motion,
        matte=np.array([[0.0, 1.0]], dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(
            persistence=1.0,
            block_size=1,
            motion_quantization=0.0,
        ),
    )

    np.testing.assert_array_equal(output[0, 1], beauty[0, 1])


def test_out_of_bounds_warped_history_falls_back_to_clean_beauty() -> None:
    beauty = _rgba(1, 2, 0.25)
    previous = FeedbackState(_rgba(1, 2, 1.0), np.ones((1, 2), dtype=np.float32), frame_number=1)
    motion = _motion(1, 2)
    motion[..., 0] = 10.0

    output, _state = process_frame(
        beauty=beauty,
        motion=motion,
        matte=np.ones((1, 2), dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(
            persistence=1.0,
            block_size=1,
            motion_clamp=64.0,
            motion_quantization=0.0,
        ),
    )

    np.testing.assert_array_equal(output, beauty)


@pytest.mark.parametrize("displacement", [0.0, 0.5])
def test_positive_low_coverage_history_is_preserved(displacement: float) -> None:
    beauty = _rgba(1, 2, 0.0)
    previous = FeedbackState(
        _rgba(1, 2, 1.0),
        np.full((1, 2), 1e-7, dtype=np.float32),
        frame_number=1,
    )
    motion = _motion(1, 2)
    motion[0, 1, 0] = displacement

    output, _state = process_frame(
        beauty=beauty,
        motion=motion,
        matte=np.array([[0.0, 1.0]], dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(
            persistence=1.0,
            block_size=1,
            motion_quantization=0.0,
        ),
    )

    np.testing.assert_allclose(output[0, 1], np.full(4, 1e-7, dtype=np.float32), rtol=1e-6)


def test_premultiplied_history_prevents_background_color_from_bleeding_at_matte_edge() -> None:
    beauty = _rgba(1, 2, 0.0)
    history = _rgba(1, 2, 100.0)
    history[0, 0] = 1.0
    previous = FeedbackState(history, np.array([[1.0, 0.0]], dtype=np.float32), frame_number=1)
    motion = _motion(1, 2)
    motion[0, 1, 0] = 0.5

    output, _state = process_frame(
        beauty=beauty,
        motion=motion,
        matte=np.array([[0.0, 1.0]], dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(
            persistence=1.0,
            block_size=1,
            motion_quantization=0.0,
        ),
    )

    np.testing.assert_allclose(output[0, 1], np.full(4, 0.5, dtype=np.float32))


def test_block_size_larger_than_frame_does_not_expand_allocation() -> None:
    beauty = _rgba(1, 2, 0.0)
    previous = FeedbackState(_rgba(1, 2, 1.0), np.ones((1, 2), dtype=np.float32), frame_number=1)

    output, _state = process_frame(
        beauty=beauty,
        motion=_motion(1, 2),
        matte=np.ones((1, 2), dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(
            persistence=1.0,
            block_size=1_000_000_000,
            diffusion=0.1,
            refresh_probability=0.5,
        ),
    )

    assert output.shape == beauty.shape
    assert np.all(np.isfinite(output))


def test_odd_resolution_with_partial_blocks_preserves_shape_and_float32() -> None:
    height, width = 79, 101
    beauty = _rgba(height, width, 0.1)
    previous = FeedbackState(
        _rgba(height, width, 0.8),
        np.ones((height, width), dtype=np.float32),
        frame_number=1,
    )

    output, state = process_frame(
        beauty=beauty,
        motion=_motion(height, width),
        matte=np.ones((height, width), dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(persistence=0.5, block_size=16),
    )

    assert output.shape == (79, 101, 4)
    assert output.dtype == np.float32
    np.testing.assert_allclose(output, _rgba(height, width, 0.45), atol=1e-7)
    assert state.history_matte.shape == (79, 101)


@pytest.mark.parametrize(
    ("mode", "channels", "reverse", "output_digest", "matte_digest"),
    [
        (
            FeedbackMode.HARD_LOCALIZED,
            MotionChannels.RG,
            True,
            "ff8b4547ef26ebc1fec4771f4f30f3e75ab6461c187cfa1893c498b536bef24d",
            "e8de9f98cd3d3bdfae0814809225b6b26ca94aa6fa6bd2c2dfc3984a666a3dac",
        ),
        (
            FeedbackMode.TRAIL,
            MotionChannels.BA,
            False,
            "91293ce961af06f27a9cee2f04df77aeac9605ca75370b7109f25ea8114996d0",
            "0019af03c8312cc2216ae05a9f576e2e5fd216b23702073e9caa6529514489c8",
        ),
    ],
)
def test_compact_block_refactor_preserves_feedback_bytes(
    mode: FeedbackMode,
    channels: MotionChannels,
    reverse: bool,
    output_digest: str,
    matte_digest: str,
) -> None:
    """Lock representative pre-refactor float32 bytes for both feedback modes."""
    height, width = 5, 7
    y, x = np.indices((height, width), dtype=np.float32)
    beauty = np.stack((x / 10, y / 10, (x + y) / 20, np.ones_like(x)), axis=-1).astype(np.float32)
    history = np.stack(
        ((x + 1) / 8, (y + 1) / 6, (x + 2 * y) / 16, np.ones_like(x)), axis=-1
    ).astype(np.float32)
    previous = FeedbackState(
        history,
        np.where((x + y) % 4 == 0, 0.25, 1.0).astype(np.float32),
        frame_number=10,
    )
    matte = np.where((2 * x + y) % 5 == 0, 0.25, np.where((x + y) % 3 == 0, 0, 1)).astype(
        np.float32
    )
    motion = np.stack(
        ((x % 3) - 1, (y % 3) - 1, ((x + y) % 4) - 1.5, ((2 * y + x) % 5) - 2),
        axis=-1,
    ).astype(np.float32)

    output, state = process_frame(
        beauty,
        motion,
        matte,
        previous,
        frame_number=11,
        settings=FeedbackSettings(
            mode=mode,
            trail_decay=0.73,
            persistence=0.81,
            block_size=3,
            motion_channels=channels,
            reverse_motion=reverse,
            flip_x=True,
            flip_y=True,
            motion_gain=1.7,
            motion_clamp=1.9,
            motion_quantization=0.25,
            diffusion=0.2,
            refresh_probability=0.35,
            seed=23,
        ),
    )

    assert hashlib.sha256(output.tobytes()).hexdigest() == output_digest
    assert hashlib.sha256(state.history_matte.tobytes()).hexdigest() == matte_digest


def test_extreme_quantization_and_diffusion_remain_numerically_safe() -> None:
    beauty = _rgba(2, 2, 0.25)
    previous = FeedbackState(_rgba(2, 2, 1.0), np.ones((2, 2), dtype=np.float32), frame_number=1)
    motion = _motion(2, 2)
    motion[..., 0] = np.finfo(np.float32).max

    with np.errstate(over="raise", invalid="raise"):
        output, state = process_frame(
            beauty=beauty,
            motion=motion,
            matte=np.ones((2, 2), dtype=np.float32),
            previous_state=previous,
            frame_number=2,
            settings=FeedbackSettings(
                persistence=1.0,
                block_size=1,
                motion_clamp=float(np.finfo(np.float32).max),
                motion_quantization=1e300,
                diffusion=1e300,
            ),
        )

    assert np.all(np.isfinite(output))
    assert np.all(np.isfinite(state.history))


def test_refresh_probability_one_uses_clean_beauty_for_every_block() -> None:
    beauty = _rgba(3, 5, 0.2)
    previous = FeedbackState(_rgba(3, 5, 1.0), np.ones((3, 5), dtype=np.float32), frame_number=1)

    output, _state = process_frame(
        beauty=beauty,
        motion=_motion(3, 5),
        matte=np.ones((3, 5), dtype=np.float32),
        previous_state=previous,
        frame_number=2,
        settings=FeedbackSettings(
            persistence=1.0,
            block_size=2,
            refresh_probability=1.0,
        ),
    )

    np.testing.assert_array_equal(output, beauty)


def test_partial_refresh_is_deterministic_per_seed_frame_and_block() -> None:
    beauty = _rgba(8, 8, 0.0)
    previous = FeedbackState(_rgba(8, 8, 1.0), np.ones((8, 8), dtype=np.float32), frame_number=3)

    def process(seed: int, frame_number: int = 4) -> np.ndarray:
        output, _state = process_frame(
            beauty=beauty,
            motion=_motion(8, 8),
            matte=np.ones((8, 8), dtype=np.float32),
            previous_state=previous,
            frame_number=frame_number,
            settings=FeedbackSettings(
                persistence=1.0,
                block_size=2,
                refresh_probability=0.5,
                seed=seed,
            ),
        )
        return output

    first = process(0)
    repeated = process(0)
    other_seed = process(1)
    other_frame = process(0, frame_number=5)

    np.testing.assert_array_equal(first, repeated)
    assert not np.array_equal(first, other_seed)
    assert not np.array_equal(first, other_frame)
    block_values = []
    for y0 in range(0, 8, 2):
        for x0 in range(0, 8, 2):
            block = first[y0 : y0 + 2, x0 : x0 + 2]
            np.testing.assert_array_equal(block, np.full_like(block, block[0, 0, 0]))
            block_values.append(float(block[0, 0, 0]))
    assert set(block_values) == {0.0, 1.0}
