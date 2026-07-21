import numpy as np
import pytest

from object_datamosh.core.block_preparation import PreparedBlocks
from object_datamosh.core.feedback import _apply_refresh


def _reference_refresh(
    prepared: PreparedBlocks,
    candidate: np.ndarray,
    covered: np.ndarray,
    localized_history: np.ndarray,
    persistence: float,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Retain the pre-optimization block-loop behavior as a correctness oracle."""
    height, width = candidate.shape
    refreshed = np.repeat(
        np.repeat(prepared.refresh, prepared.block_size, axis=0),
        prepared.block_size,
        axis=1,
    )[:height, :width]
    block_candidates = np.zeros(prepared.refresh.shape, dtype=bool)
    active_pixels = candidate & covered & (persistence > 0.0)
    for block_y in range(block_candidates.shape[0]):
        for block_x in range(block_candidates.shape[1]):
            y0 = block_y * prepared.block_size
            x0 = block_x * prepared.block_size
            block_candidates[block_y, block_x] = bool(
                np.any(
                    active_pixels[
                        y0 : y0 + prepared.block_size,
                        x0 : x0 + prepared.block_size,
                    ]
                )
            )
    unrefreshed_blend = persistence * localized_history * covered
    refresh_restored = refreshed & (unrefreshed_blend > 0.0)
    blend = (unrefreshed_blend * ~refreshed)[..., None]
    refresh_blocks = int(np.count_nonzero(prepared.refresh & block_candidates))
    return blend, refresh_restored, refresh_blocks


@pytest.mark.parametrize(
    ("shape", "block_size", "persistence"),
    [
        ((1, 1), 1, 0.0),
        ((1, 1), 4, 1.0),
        ((3, 5), 2, 0.75),
        ((5, 3), 2, 1.0),
    ],
)
def test_vectorized_refresh_diagnostics_match_block_loop_reference(
    shape: tuple[int, int], block_size: int, persistence: float
) -> None:
    height, width = shape
    y, x = np.indices(shape)
    candidate = (x + 2 * y) % 3 == 0
    covered = (2 * x + y) % 4 != 0
    localized_history = np.where(candidate, 1.0, 0.25).astype(np.float32)
    block_rows = (height + block_size - 1) // block_size
    block_columns = (width + block_size - 1) // block_size
    refresh = (np.indices((block_rows, block_columns)).sum(axis=0) % 2 == 0).astype(np.bool_)
    prepared = PreparedBlocks(
        displacement=np.zeros((block_rows, block_columns, 2), dtype=np.float32),
        refresh=refresh,
        block_size=block_size,
        frame_shape=shape,
    )

    actual = _apply_refresh(prepared, candidate, covered, localized_history, persistence)
    expected = _reference_refresh(prepared, candidate, covered, localized_history, persistence)

    np.testing.assert_array_equal(actual[0], expected[0])
    np.testing.assert_array_equal(actual[1], expected[1])
    assert actual[2] == expected[2]
