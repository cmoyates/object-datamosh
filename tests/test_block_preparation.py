import numpy as np

from object_datamosh.core.block_preparation import prepare_blocks
from object_datamosh.core.contracts import FeedbackSettings, MotionChannels


def test_prepare_blocks_returns_compact_weighted_grids_for_partial_edges() -> None:
    motion = np.zeros((3, 5, 4), dtype=np.float32)
    motion[..., 0] = np.array(
        [
            [0.0, 2.0, 4.0, 6.0, 8.0],
            [2.0, 4.0, 6.0, 8.0, 10.0],
            [10.0, 12.0, 14.0, 16.0, 18.0],
        ],
        dtype=np.float32,
    )
    matte = np.array(
        [
            [1.0, 1.0, 1.0, 1.0, 0.0],
            [1.0, 1.0, 1.0, 1.0, 1.0],
            [1.0, 1.0, 0.0, 1.0, 1.0],
        ],
        dtype=np.float32,
    )

    prepared = prepare_blocks(
        motion,
        matte,
        frame_number=7,
        settings=FeedbackSettings(
            block_size=2,
            motion_clamp=100.0,
            motion_quantization=0.0,
        ),
    )

    assert prepared.frame_shape == (3, 5)
    assert prepared.block_size == 2
    assert prepared.displacement.shape == (2, 3, 2)
    assert prepared.displacement.dtype == np.float32
    np.testing.assert_array_equal(
        prepared.displacement[..., 0],
        np.array([[2.0, 6.0, 10.0], [11.0, 16.0, 18.0]], dtype=np.float32),
    )
    np.testing.assert_array_equal(prepared.displacement[..., 1], np.zeros((2, 3), np.float32))
    np.testing.assert_array_equal(prepared.refresh, np.zeros((2, 3), dtype=np.bool_))


def test_prepare_blocks_retains_motion_decode_clamp_and_quantization_meaning() -> None:
    motion = np.zeros((1, 2, 4), dtype=np.float32)
    motion[0, 0, 2:] = (3.0, -4.0)
    motion[0, 1, 2:] = (-0.6, 0.6)

    prepared = prepare_blocks(
        motion,
        np.ones((1, 2), dtype=np.float32),
        frame_number=1,
        settings=FeedbackSettings(
            block_size=1,
            motion_channels=MotionChannels.BA,
            reverse_motion=True,
            flip_x=True,
            flip_y=True,
            motion_gain=2.0,
            motion_clamp=5.0,
            motion_quantization=1.0,
        ),
    )

    np.testing.assert_array_equal(
        prepared.displacement,
        np.array([[[3.0, -4.0], [-1.0, 1.0]]], dtype=np.float32),
    )


def test_prepare_blocks_returns_deterministic_compact_diffusion_and_refresh() -> None:
    settings = FeedbackSettings(
        block_size=2,
        motion_quantization=0.0,
        diffusion=0.25,
        refresh_probability=0.5,
        seed=17,
    )
    motion = np.zeros((3, 5, 4), dtype=np.float32)
    matte = np.ones((3, 5), dtype=np.float32)

    prepared = prepare_blocks(motion, matte, frame_number=4, settings=settings)
    repeated = prepare_blocks(motion, matte, frame_number=4, settings=settings)
    other_frame = prepare_blocks(motion, matte, frame_number=5, settings=settings)

    np.testing.assert_array_equal(prepared.displacement, repeated.displacement)
    np.testing.assert_array_equal(prepared.refresh, repeated.refresh)
    np.testing.assert_allclose(
        prepared.displacement,
        np.array(
            [
                [[0.05323072, -0.24754515], [0.01144829, -0.21040809], [-0.05177774, -0.23570475]],
                [[-0.00952731, 0.16988355], [-0.05223134, -0.09983194], [0.13204958, -0.01923102]],
            ],
            dtype=np.float32,
        ),
        rtol=0.0,
        atol=5e-9,
    )
    np.testing.assert_array_equal(
        prepared.refresh,
        np.array([[False, True, False], [False, False, False]], dtype=np.bool_),
    )
    assert not np.array_equal(prepared.displacement, other_frame.displacement)
    assert not np.array_equal(prepared.refresh, other_frame.refresh)
