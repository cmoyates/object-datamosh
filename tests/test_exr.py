from __future__ import annotations

import numpy as np
import pytest

from object_datamosh.core.exr import _undo_zip_preprocessing


def _reference_undo_zip_preprocessing(value: bytes) -> bytes:
    predicted = bytearray(value)
    for index in range(1, len(predicted)):
        predicted[index] = (predicted[index - 1] + predicted[index] - 128) & 0xFF
    half = (len(predicted) + 1) // 2
    restored = bytearray(len(predicted))
    restored[0::2] = predicted[:half]
    restored[1::2] = predicted[half:]
    return bytes(restored)


@pytest.mark.parametrize(
    "predicted",
    [
        b"",
        b"\x00",
        b"\xff",
        bytes([0] * 32),
        bytes([255] * 32),
        bytes([0, 255] * 17),
        bytes(range(31)),
        bytes(range(32)),
    ],
)
def test_zip_predictor_reversal_matches_reference_for_edge_patterns(predicted: bytes) -> None:
    assert _undo_zip_preprocessing(predicted) == _reference_undo_zip_preprocessing(predicted)


def test_zip_predictor_reversal_matches_reference_for_random_lengths() -> None:
    rng = np.random.default_rng(73073)

    for length in (*range(65), 127, 128, 129, 1023, 1024, 1025, 65_537):
        predicted = rng.integers(0, 256, size=length, dtype=np.uint8).tobytes()

        assert _undo_zip_preprocessing(predicted) == _reference_undo_zip_preprocessing(predicted)
