"""Bounded, Blender-independent feedback diagnostics and near-no-op assessment."""

from dataclasses import asdict, dataclass, fields
from typing import Literal

from .contracts import FeedbackSettings, HistorySource, InvalidHistoryFallback

# Efficacy is assessed only after two non-reset frames with a non-empty target. A run is a
# likely near-no-op when both history use is at most 5% of attempts and changed output is at most
# 1% of pixels. Supporting causes use inclusive 80% "mostly" thresholds. These constants are
# deliberately public so reports, documentation, and tests refer to one policy.
MIN_ELIGIBLE_FRAMES = 2
HISTORY_USE_RATIO_MAX = 0.05
OUTPUT_CHANGE_RATIO_MAX = 0.01
MOSTLY_INVALID_RATIO_MIN = 0.80
MOSTLY_REFRESHED_RATIO_MIN = 0.80
MAX_REPORTED_FRAMES = 96
CHANGE_EPSILON = 1.0e-6


@dataclass(frozen=True, slots=True)
class FrameDiagnostics:
    """Counters captured from the actual decisions made while processing one frame."""

    frame_number: int
    reset: bool
    pixel_count: int
    target_matte_pixels: int
    target_matte_coverage: float
    effect_matte_pixels: int
    effect_matte_coverage: float
    primary_history_attempts: int
    primary_history_valid_uses: int
    primary_history_invalid_samples: int
    same_pixel_fallback_attempts: int
    same_pixel_fallback_valid_uses: int
    current_beauty_fallback_pixels: int
    refresh_restored_pixels: int
    refresh_restored_blocks: int
    historical_blend_pixels: int
    historical_blend_weight: float
    changed_output_pixels: int
    changed_output_ratio: float
    changed_output_mean_absolute: float
    changed_output_max_absolute: float


@dataclass(frozen=True, slots=True)
class NearNoOpAssessment:
    """Advisory efficacy result; it never gates processing."""

    likely_near_no_op: bool
    causes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProcessingDiagnostics:
    """Sequence aggregation that excludes reset frames from efficacy metrics."""

    frames: tuple[FrameDiagnostics, ...]
    totals: FrameDiagnostics
    reset_frame_count: int
    eligible_frame_count: int

    @classmethod
    def from_frames(cls, frames_: tuple[FrameDiagnostics, ...]) -> "ProcessingDiagnostics":
        eligible = tuple(frame for frame in frames_ if not frame.reset)
        source = eligible
        integer_names = (
            "pixel_count",
            "target_matte_pixels",
            "effect_matte_pixels",
            "primary_history_attempts",
            "primary_history_valid_uses",
            "primary_history_invalid_samples",
            "same_pixel_fallback_attempts",
            "same_pixel_fallback_valid_uses",
            "current_beauty_fallback_pixels",
            "refresh_restored_pixels",
            "refresh_restored_blocks",
            "historical_blend_pixels",
            "changed_output_pixels",
        )
        sums = {name: sum(getattr(frame, name) for frame in source) for name in integer_names}
        pixel_count = sums["pixel_count"]
        target_coverage_sum = sum(
            frame.target_matte_coverage * frame.pixel_count for frame in source
        )
        effect_coverage_sum = sum(
            frame.effect_matte_coverage * frame.pixel_count for frame in source
        )
        changed_absolute_sum = sum(
            frame.changed_output_mean_absolute * frame.pixel_count for frame in source
        )
        totals = FrameDiagnostics(
            frame_number=0,
            reset=False,
            **sums,
            target_matte_coverage=(target_coverage_sum / pixel_count if pixel_count else 0.0),
            effect_matte_coverage=(effect_coverage_sum / pixel_count if pixel_count else 0.0),
            historical_blend_weight=sum(frame.historical_blend_weight for frame in source),
            changed_output_ratio=(
                sums["changed_output_pixels"] / pixel_count if pixel_count else 0.0
            ),
            changed_output_mean_absolute=(
                changed_absolute_sum / pixel_count if pixel_count else 0.0
            ),
            changed_output_max_absolute=max(
                (frame.changed_output_max_absolute for frame in source), default=0.0
            ),
        )
        return cls(
            frames=frames_,
            totals=totals,
            reset_frame_count=len(frames_) - len(eligible),
            eligible_frame_count=len(eligible),
        )

    @classmethod
    def empty(cls) -> "ProcessingDiagnostics":
        return cls.from_frames(())

    def to_report_payload(
        self,
        *,
        outcome: Literal["SUCCESS", "CANCELLED", "FAILURE", "RUNNING"],
        frame_start: int,
        frame_end: int,
        completed_frames: tuple[int, ...],
        configuration: dict[str, object],
        manifest_path: object,
        report_path: object,
        settings_fingerprint: str,
        warnings: tuple[str, ...] = (),
        failure: str | None = None,
    ) -> dict[str, object]:
        detailed = self.frames[-MAX_REPORTED_FRAMES:]
        prefix = {
            "count": len(completed_frames),
            "start": completed_frames[0] if completed_frames else None,
            "end": completed_frames[-1] if completed_frames else None,
        }
        diagnostic_frames = tuple(frame.frame_number for frame in self.frames)
        diagnostic_prefix = {
            "count": len(diagnostic_frames),
            "start": diagnostic_frames[0] if diagnostic_frames else None,
            "end": diagnostic_frames[-1] if diagnostic_frames else None,
        }
        if diagnostic_frames == completed_frames:
            availability = "COMPLETE"
        elif not diagnostic_frames:
            availability = "UNAVAILABLE"
        else:
            availability = "PARTIAL"
        return {
            "schema_version": 1,
            "manifest_schema_version": 5,
            "terminal_outcome": outcome,
            "frame_range": {"start": frame_start, "end": frame_end},
            "completed_prefix": prefix,
            "diagnostics_completed_prefix": diagnostic_prefix,
            "diagnostics_availability": availability,
            "configuration": configuration,
            "settings_fingerprint": settings_fingerprint,
            "agreement": {
                "history_source": configuration.get("history_source"),
                "completed_prefix": prefix,
                "settings_fingerprint": settings_fingerprint,
            },
            "manifest_path": str(manifest_path),
            "report_path": str(report_path),
            "reset_frame_count": self.reset_frame_count,
            "eligible_frame_count": self.eligible_frame_count,
            "totals": asdict(self.totals),
            "frames": [asdict(frame) for frame in detailed],
            "frame_diagnostics_omitted": len(self.frames) - len(detailed),
            "warnings": list(warnings),
            "failure": failure,
        }


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0.0 else 0.0


def assess_near_no_op(
    diagnostics: ProcessingDiagnostics, settings: FeedbackSettings
) -> NearNoOpAssessment:
    """Return deterministic advisory evidence without treating artistic controls as errors."""
    totals = diagnostics.totals
    if diagnostics.eligible_frame_count >= MIN_ELIGIBLE_FRAMES and totals.target_matte_pixels == 0:
        return NearNoOpAssessment(False, ("empty target matte",))
    if diagnostics.eligible_frame_count < MIN_ELIGIBLE_FRAMES or totals.target_matte_pixels == 0:
        return NearNoOpAssessment(False, ())

    # Valid samples are only candidates: refresh and zero persistence can still restore current
    # beauty. Measure pixels that actually received historical weight so refresh-driven no-ops
    # remain diagnosable.
    history_use_ratio = _ratio(
        totals.historical_blend_pixels,
        totals.primary_history_attempts,
    )
    near_no_op = (
        history_use_ratio <= HISTORY_USE_RATIO_MAX
        and totals.changed_output_ratio <= OUTPUT_CHANGE_RATIO_MAX
    )
    if not near_no_op:
        return NearNoOpAssessment(False, ())

    causes: list[str] = []
    if settings.history_source is HistorySource.TARGET_ONLY:
        causes.append("Target Only history source is selected")
    invalid_ratio = _ratio(totals.primary_history_invalid_samples, totals.primary_history_attempts)
    if invalid_ratio >= MOSTLY_INVALID_RATIO_MIN:
        causes.append("primary history is mostly out of bounds or invalid")
        causes.append("vector direction or convention may be wrong")
    if (
        totals.primary_history_invalid_samples > 0
        and settings.invalid_history_fallback is not InvalidHistoryFallback.SAME_PIXEL_HISTORY
    ):
        causes.append("Same Pixel History fallback is not selected")
    refresh_ratio = _ratio(totals.refresh_restored_pixels, totals.effect_matte_pixels)
    if refresh_ratio >= MOSTLY_REFRESHED_RATIO_MIN:
        causes.append("Refresh restores too much clean beauty")
    if not causes:
        causes.append("measured history use and output change are both very low")
    return NearNoOpAssessment(True, tuple(causes))


def diagnostic_counter_names() -> tuple[str, ...]:
    """Expose the stable report counter contract for invariant tests and documentation."""
    return tuple(field.name for field in fields(FrameDiagnostics))
