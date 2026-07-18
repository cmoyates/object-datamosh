import numpy as np
import pytest

from object_datamosh.core.sampling import bilinear_sample


def test_bilinear_sampling_at_pixel_centers_is_identity() -> None:
    image = np.arange(12, dtype=np.float32).reshape(3, 4)
    sample_y, sample_x = np.indices(image.shape, dtype=np.float32)

    sampled, valid = bilinear_sample(image, sample_x, sample_y)

    np.testing.assert_array_equal(sampled, image)
    np.testing.assert_array_equal(valid, np.ones(image.shape, dtype=np.bool_))


def test_bilinear_sampling_at_offset_integer_coordinates() -> None:
    image = np.arange(12, dtype=np.float32).reshape(3, 4)

    sampled, valid = bilinear_sample(
        image,
        np.array([[1.0, 3.0]], dtype=np.float32),
        np.array([[2.0, 0.0]], dtype=np.float32),
    )

    np.testing.assert_array_equal(sampled, np.array([[9.0, 3.0]], dtype=np.float32))
    np.testing.assert_array_equal(valid, np.ones((1, 2), dtype=np.bool_))


def test_bilinear_sampling_interpolates_fractional_channel_coordinates() -> None:
    image = np.array(
        [
            [[0.0, 10.0], [2.0, 12.0]],
            [[4.0, 14.0], [6.0, 16.0]],
        ],
        dtype=np.float32,
    )

    sampled, valid = bilinear_sample(
        image,
        np.array([[0.5]], dtype=np.float32),
        np.array([[0.25]], dtype=np.float32),
    )

    np.testing.assert_allclose(sampled, np.array([[[2.0, 12.0]]], dtype=np.float32))
    np.testing.assert_array_equal(valid, np.array([[True]]))


def test_bilinear_sampling_rejects_out_of_bounds_without_wrapping() -> None:
    image = np.array([[1.0, 2.0], [3.0, 99.0]], dtype=np.float32)
    coordinates = np.array([[-0.25, 1.25, np.nan]], dtype=np.float32)
    sample_y = np.array([[0.0, 1.0, 0.0]], dtype=np.float32)

    sampled, valid = bilinear_sample(image, coordinates, sample_y)

    np.testing.assert_array_equal(sampled, np.zeros((1, 3), dtype=np.float32))
    np.testing.assert_array_equal(valid, np.zeros((1, 3), dtype=np.bool_))


def test_bilinear_sampling_requires_float32_numpy_inputs() -> None:
    image = np.ones((1, 1), dtype=np.float32)
    coordinates = np.zeros((1, 1), dtype=np.float32)

    with pytest.raises(TypeError, match="image must use float32"):
        bilinear_sample(image.astype(np.float64), coordinates, coordinates)
    with pytest.raises(TypeError, match="sample_x must be a NumPy array"):
        bilinear_sample(image, [[0.0]], coordinates)  # ty: ignore[invalid-argument-type]


def test_bilinear_sampling_rejects_empty_images() -> None:
    coordinates = np.zeros((1, 1), dtype=np.float32)

    with pytest.raises(ValueError, match="image dimensions must be nonzero"):
        bilinear_sample(np.empty((0, 1), dtype=np.float32), coordinates, coordinates)
