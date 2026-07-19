import numpy as np
import pytest

from object_datamosh.core.contracts import (
    FeedbackMode,
    FeedbackSettings,
    FeedbackState,
    MotionChannels,
)
from object_datamosh.core.feedback import process_frame


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
        settings=FeedbackSettings(persistence=1.0),
        force_reset=True,
    )

    np.testing.assert_array_equal(output, beauty)
    np.testing.assert_array_equal(state.history, beauty)


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
