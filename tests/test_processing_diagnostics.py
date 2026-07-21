import json
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from object_datamosh.core import feedback
from object_datamosh.core.contracts import (
    FeedbackSettings,
    FeedbackState,
    HistorySource,
    InvalidHistoryFallback,
)
from object_datamosh.core.diagnostics import (
    FrameDiagnostics,
    ProcessingDiagnostics,
    assess_near_no_op,
    diagnostic_counter_names,
)
from object_datamosh.core.feedback import process_frame_with_diagnostics
from object_datamosh.core.paths import SequencePaths
from object_datamosh.sequence_processing import processing_report_path


def _rgba(height: int, width: int, value: float) -> np.ndarray:
    return np.full((height, width, 4), value, dtype=np.float32)


def _frame(**changes: object) -> FrameDiagnostics:
    values: dict[str, object] = {
        "frame_number": 2,
        "reset": False,
        "pixel_count": 100,
        "target_matte_pixels": 100,
        "target_matte_coverage": 1.0,
        "effect_matte_pixels": 100,
        "effect_matte_coverage": 1.0,
        "primary_history_attempts": 100,
        "primary_history_valid_uses": 100,
        "primary_history_invalid_samples": 0,
        "same_pixel_fallback_attempts": 0,
        "same_pixel_fallback_valid_uses": 0,
        "current_beauty_fallback_pixels": 0,
        "refresh_restored_pixels": 0,
        "refresh_restored_blocks": 0,
        "historical_blend_pixels": 100,
        "historical_blend_weight": 100.0,
        "changed_output_pixels": 100,
        "changed_output_ratio": 1.0,
        "changed_output_mean_absolute": 0.5,
        "changed_output_max_absolute": 1.0,
    }
    values.update(changes)
    return FrameDiagnostics(**values)  # type: ignore[arg-type]


def test_frame_diagnostics_count_actual_primary_fallback_refresh_and_change_decisions() -> None:
    beauty = _rgba(1, 3, 0.0)
    previous = FeedbackState(
        history=_rgba(1, 3, 1.0),
        history_matte=np.ones((1, 3), dtype=np.float32),
        frame_number=1,
    )
    motion = _rgba(1, 3, 0.0)
    motion[..., 0] = np.array([[3.0, 2.0, 0.0]], dtype=np.float32)
    matte = np.ones((1, 3), dtype=np.float32)

    output, _state, diagnostic = process_frame_with_diagnostics(
        beauty,
        motion,
        matte,
        previous,
        2,
        FeedbackSettings(
            history_source=HistorySource.FULL_FRAME,
            invalid_history_fallback=InvalidHistoryFallback.SAME_PIXEL_HISTORY,
            persistence=1.0,
            block_size=1,
        ),
    )

    np.testing.assert_array_equal(output, _rgba(1, 3, 1.0))
    assert diagnostic.primary_history_attempts == 3
    assert diagnostic.primary_history_valid_uses == 1
    assert diagnostic.primary_history_invalid_samples == 2
    assert diagnostic.same_pixel_fallback_attempts == 2
    assert diagnostic.same_pixel_fallback_valid_uses == 2
    assert diagnostic.current_beauty_fallback_pixels == 0
    assert diagnostic.historical_blend_pixels == 3
    assert diagnostic.changed_output_pixels == 3
    assert diagnostic.changed_output_ratio == 1.0


def test_aggregation_preserves_every_counter_and_excludes_reset_frames() -> None:
    reset = _frame(frame_number=1, reset=True, pixel_count=10_000)
    run = ProcessingDiagnostics.from_frames((_frame(), _frame(frame_number=3), reset))
    totals = run.totals

    assert diagnostic_counter_names() == (
        "frame_number",
        "reset",
        "pixel_count",
        "target_matte_pixels",
        "target_matte_coverage",
        "effect_matte_pixels",
        "effect_matte_coverage",
        "primary_history_attempts",
        "primary_history_valid_uses",
        "primary_history_invalid_samples",
        "same_pixel_fallback_attempts",
        "same_pixel_fallback_valid_uses",
        "current_beauty_fallback_pixels",
        "refresh_restored_pixels",
        "refresh_restored_blocks",
        "historical_blend_pixels",
        "historical_blend_weight",
        "changed_output_pixels",
        "changed_output_ratio",
        "changed_output_mean_absolute",
        "changed_output_max_absolute",
    )
    assert totals.frame_number == 0
    assert not totals.reset
    assert totals.pixel_count == 200
    assert totals.target_matte_pixels == 200
    assert totals.target_matte_coverage == 1.0
    assert totals.effect_matte_pixels == 200
    assert totals.effect_matte_coverage == 1.0
    assert totals.primary_history_attempts == 200
    assert totals.primary_history_valid_uses == 200
    assert totals.primary_history_invalid_samples == 0
    assert totals.same_pixel_fallback_attempts == 0
    assert totals.same_pixel_fallback_valid_uses == 0
    assert totals.current_beauty_fallback_pixels == 0
    assert totals.refresh_restored_pixels == 0
    assert totals.refresh_restored_blocks == 0
    assert totals.historical_blend_pixels == 200
    assert totals.historical_blend_weight == 200.0
    assert totals.changed_output_pixels == 200
    assert totals.changed_output_ratio == 1.0
    assert totals.changed_output_mean_absolute == 0.5
    assert totals.changed_output_max_absolute == 1.0


def test_frame_diagnostics_count_current_beauty_fallback() -> None:
    beauty = _rgba(1, 2, 0.25)
    previous = FeedbackState(
        history=_rgba(1, 2, 1.0),
        history_matte=np.ones((1, 2), dtype=np.float32),
        frame_number=1,
    )
    motion = _rgba(1, 2, 0.0)
    motion[..., 0] = 10.0

    output, _state, diagnostic = process_frame_with_diagnostics(
        beauty,
        motion,
        np.ones((1, 2), dtype=np.float32),
        previous,
        2,
        FeedbackSettings(
            history_source=HistorySource.FULL_FRAME,
            invalid_history_fallback=InvalidHistoryFallback.CURRENT_BEAUTY,
            persistence=1.0,
            block_size=1,
        ),
    )

    np.testing.assert_array_equal(output, beauty)
    assert diagnostic.primary_history_attempts == 2
    assert diagnostic.primary_history_invalid_samples == 2
    assert diagnostic.current_beauty_fallback_pixels == 2
    assert diagnostic.refresh_restored_pixels == 0
    assert diagnostic.refresh_restored_blocks == 0
    assert diagnostic.historical_blend_pixels == 0
    assert diagnostic.changed_output_mean_absolute == 0.0
    assert diagnostic.changed_output_max_absolute == 0.0


@pytest.mark.parametrize("refresh_probability", [0.0, 1e-12])
def test_processing_skips_refresh_expansion_when_no_blocks_are_selected(
    monkeypatch: pytest.MonkeyPatch, refresh_probability: float
) -> None:
    beauty = _rgba(1, 1, 0.25)
    previous = FeedbackState(
        history=_rgba(1, 1, 1.0),
        history_matte=np.ones((1, 1), dtype=np.float32),
        frame_number=1,
    )
    original_expand = feedback._expand_blocks

    def reject_refresh_expansion(
        block_values: np.ndarray, block_size: int, height: int, width: int
    ) -> np.ndarray:
        if block_values.dtype == np.bool_:
            pytest.fail("an all-false refresh grid must not be expanded")
        return original_expand(block_values, block_size, height, width)

    monkeypatch.setattr(feedback, "_expand_blocks", reject_refresh_expansion)

    output, _state, diagnostic = process_frame_with_diagnostics(
        beauty,
        _rgba(1, 1, 0.0),
        np.ones((1, 1), dtype=np.float32),
        previous,
        2,
        FeedbackSettings(
            persistence=1.0,
            refresh_probability=refresh_probability,
            block_size=1,
            seed=0,
        ),
    )

    np.testing.assert_array_equal(output, _rgba(1, 1, 1.0))
    assert diagnostic.refresh_restored_pixels == 0
    assert diagnostic.refresh_restored_blocks == 0


def test_frame_diagnostics_count_refresh_that_actually_restores_beauty() -> None:
    beauty = _rgba(1, 2, 0.25)
    previous = FeedbackState(
        history=_rgba(1, 2, 1.0),
        history_matte=np.ones((1, 2), dtype=np.float32),
        frame_number=1,
    )

    output, _state, diagnostic = process_frame_with_diagnostics(
        beauty,
        _rgba(1, 2, 0.0),
        np.ones((1, 2), dtype=np.float32),
        previous,
        2,
        FeedbackSettings(persistence=1.0, refresh_probability=1.0, block_size=1),
    )

    np.testing.assert_array_equal(output, beauty)
    assert diagnostic.primary_history_valid_uses == 2
    assert diagnostic.refresh_restored_pixels == 2
    assert diagnostic.refresh_restored_blocks == 2
    assert diagnostic.historical_blend_pixels == 0
    assert diagnostic.historical_blend_weight == 0.0


def test_reset_diagnostics_are_identified_and_excluded_from_efficacy_totals() -> None:
    _output, _state, reset = process_frame_with_diagnostics(
        _rgba(1, 2, 0.25),
        _rgba(1, 2, 0.0),
        np.ones((1, 2), dtype=np.float32),
        None,
        1,
        FeedbackSettings(),
    )
    run = ProcessingDiagnostics.from_frames((reset, _frame()))

    assert reset.reset
    assert reset.primary_history_attempts == 0
    assert run.reset_frame_count == 1
    assert run.eligible_frame_count == 1
    assert run.totals.primary_history_attempts == 100


@pytest.mark.parametrize("ratio, warns", [(0.05, True), (0.050001, False)])
def test_near_no_op_history_use_threshold_is_inclusive(ratio: float, warns: bool) -> None:
    frame = _frame(
        primary_history_valid_uses=round(100 * ratio, 6),
        historical_blend_pixels=round(100 * ratio, 6),
        changed_output_pixels=1,
        changed_output_ratio=0.01,
    )
    assessment = assess_near_no_op(
        ProcessingDiagnostics.from_frames(
            (
                frame,
                _frame(
                    frame_number=3,
                    **{
                        "primary_history_valid_uses": round(100 * ratio, 6),
                        "historical_blend_pixels": round(100 * ratio, 6),
                        "changed_output_pixels": 1,
                        "changed_output_ratio": 0.01,
                    },
                ),
            )
        ),
        FeedbackSettings(),
    )
    assert assessment.likely_near_no_op is warns


@pytest.mark.parametrize("ratio, warns", [(0.01, True), (0.010001, False)])
def test_near_no_op_change_threshold_is_inclusive(ratio: float, warns: bool) -> None:
    pixel_count = 1_000_000
    changed_pixels = round(pixel_count * ratio)
    frame = _frame(
        pixel_count=pixel_count,
        primary_history_attempts=100,
        primary_history_valid_uses=5,
        historical_blend_pixels=5,
        changed_output_pixels=changed_pixels,
        changed_output_ratio=ratio,
    )
    assessment = assess_near_no_op(
        ProcessingDiagnostics.from_frames(
            (
                frame,
                _frame(
                    frame_number=3,
                    pixel_count=pixel_count,
                    primary_history_attempts=100,
                    primary_history_valid_uses=5,
                    historical_blend_pixels=5,
                    changed_output_pixels=changed_pixels,
                    changed_output_ratio=ratio,
                ),
            )
        ),
        FeedbackSettings(),
    )
    assert assessment.likely_near_no_op is warns


@pytest.mark.parametrize("invalid_samples, has_cause", [(800, True), (799, False)])
def test_mostly_invalid_cause_threshold_is_inclusive(invalid_samples: int, has_cause: bool) -> None:
    frame = _frame(
        primary_history_attempts=1_000,
        primary_history_valid_uses=0,
        primary_history_invalid_samples=invalid_samples,
        historical_blend_pixels=0,
        changed_output_pixels=0,
        changed_output_ratio=0.0,
    )
    run = ProcessingDiagnostics.from_frames(
        (
            frame,
            _frame(
                frame_number=3,
                **{
                    "primary_history_attempts": 1_000,
                    "primary_history_valid_uses": 0,
                    "primary_history_invalid_samples": invalid_samples,
                    "historical_blend_pixels": 0,
                    "changed_output_pixels": 0,
                    "changed_output_ratio": 0.0,
                },
            ),
        )
    )

    causes = assess_near_no_op(run, FeedbackSettings()).causes
    assert ("primary history is mostly out of bounds or invalid" in causes) is has_cause


@pytest.mark.parametrize("refreshed_pixels, has_cause", [(80, True), (79, False)])
def test_mostly_refreshed_cause_threshold_is_inclusive(
    refreshed_pixels: int, has_cause: bool
) -> None:
    frame = _frame(
        primary_history_valid_uses=0,
        primary_history_invalid_samples=100,
        historical_blend_pixels=0,
        refresh_restored_pixels=refreshed_pixels,
        changed_output_pixels=0,
        changed_output_ratio=0.0,
    )
    run = ProcessingDiagnostics.from_frames(
        (
            frame,
            _frame(
                frame_number=3,
                **{
                    "primary_history_valid_uses": 0,
                    "primary_history_invalid_samples": 100,
                    "historical_blend_pixels": 0,
                    "refresh_restored_pixels": refreshed_pixels,
                    "changed_output_pixels": 0,
                    "changed_output_ratio": 0.0,
                },
            ),
        )
    )

    causes = assess_near_no_op(run, FeedbackSettings()).causes
    assert ("Refresh restores too much clean beauty" in causes) is has_cause


def test_valid_history_fully_restored_by_refresh_warns_with_refresh_evidence() -> None:
    refreshed = _frame(
        primary_history_valid_uses=100,
        refresh_restored_pixels=100,
        refresh_restored_blocks=100,
        historical_blend_pixels=0,
        historical_blend_weight=0.0,
        changed_output_pixels=0,
        changed_output_ratio=0.0,
    )
    assessment = assess_near_no_op(
        ProcessingDiagnostics.from_frames(
            (
                refreshed,
                _frame(
                    frame_number=3,
                    **{
                        "primary_history_valid_uses": 100,
                        "refresh_restored_pixels": 100,
                        "refresh_restored_blocks": 100,
                        "historical_blend_pixels": 0,
                        "historical_blend_weight": 0.0,
                        "changed_output_pixels": 0,
                        "changed_output_ratio": 0.0,
                    },
                ),
            )
        ),
        FeedbackSettings(refresh_probability=1.0),
    )

    assert assessment.likely_near_no_op
    assert "Refresh restores too much clean beauty" in assessment.causes


def test_effective_extreme_and_deliberately_low_persistence_do_not_warn() -> None:
    effective = ProcessingDiagnostics.from_frames((_frame(), _frame(frame_number=3)))
    low_persistence = FeedbackSettings(persistence=0.01)

    assert not assess_near_no_op(effective, FeedbackSettings()).likely_near_no_op
    assert not assess_near_no_op(effective, low_persistence).likely_near_no_op


def test_empty_matte_gets_specific_diagnostic_not_generic_near_no_op() -> None:
    empty = _frame(
        target_matte_pixels=0,
        target_matte_coverage=0.0,
        primary_history_attempts=0,
        primary_history_valid_uses=0,
        historical_blend_pixels=0,
        changed_output_pixels=0,
        changed_output_ratio=0.0,
    )
    assessment = assess_near_no_op(
        ProcessingDiagnostics.from_frames(
            (
                empty,
                _frame(
                    frame_number=3,
                    target_matte_pixels=0,
                    target_matte_coverage=0.0,
                    primary_history_attempts=0,
                    primary_history_valid_uses=0,
                    historical_blend_pixels=0,
                    changed_output_pixels=0,
                    changed_output_ratio=0.0,
                ),
            )
        ),
        FeedbackSettings(),
    )

    assert not assessment.likely_near_no_op
    assert "empty target matte" in assessment.causes


def test_report_path_is_beside_manifest() -> None:
    paths = SequencePaths(Path("/tmp/run"))
    assert processing_report_path(paths) == Path("/tmp/run/processed/ODM_processing_report.json")


def test_processing_report_is_deterministic_bounded_and_records_terminal_prefix(
    tmp_path: Path,
) -> None:
    # The session-facing serializer caps detailed frames while preserving truthful totals/prefix.
    diagnostics = ProcessingDiagnostics.from_frames(
        tuple(_frame(frame_number=n) for n in range(1, 400))
    )
    payload = diagnostics.to_report_payload(
        outcome="CANCELLED",
        frame_start=1,
        frame_end=500,
        completed_frames=tuple(range(1, 400)),
        configuration={"history_source": "FULL_FRAME"},
        manifest_path=tmp_path / "processed" / "ODM_sequence_manifest.json",
        report_path=tmp_path / "processed" / "ODM_processing_report.json",
        settings_fingerprint="abc",
    )
    encoded_once = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    encoded_twice = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    assert encoded_once == encoded_twice
    assert len(encoded_once.encode()) < 64 * 1024
    assert payload["schema_version"] == 1
    assert payload["terminal_outcome"] == "CANCELLED"
    assert payload["completed_prefix"] == {"count": 399, "start": 1, "end": 399}
    assert payload["frame_diagnostics_omitted"] == 303
    assert len(cast(list[Any], payload["frames"])) == 96
