import pytest

from object_datamosh.combined_failures import (
    CombinedRenderingFailure,
    render_with_frame_context,
)


def test_background_render_failure_preserves_active_frame_after_completed_prefix() -> None:
    completed = 2

    def fail_render() -> None:
        raise RuntimeError("progress display unavailable")

    with pytest.raises(CombinedRenderingFailure) as failure:
        render_with_frame_context(
            fail_render,
            frame_start=3,
            frame_end=7,
            completed_count=lambda: completed,
        )

    assert failure.value.frame == 5
    assert str(failure.value) == "progress display unavailable"


def test_background_render_finalization_failure_uses_last_configured_frame() -> None:
    def fail_finalization() -> None:
        raise RuntimeError("progress close failed")

    with pytest.raises(CombinedRenderingFailure) as failure:
        render_with_frame_context(
            fail_finalization,
            frame_start=3,
            frame_end=4,
            completed_count=lambda: 2,
        )

    assert failure.value.frame == 4
