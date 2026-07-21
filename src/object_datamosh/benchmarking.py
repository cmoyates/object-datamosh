"""Small dependency-free helpers for reproducible developer benchmark reports."""

import statistics
from collections.abc import Mapping, Sequence
from typing import Any

_PROCESSING_STAGES = (
    "beauty_read",
    "vector_read",
    "matte_read",
    "core_processing",
    "processed_exr_write",
    "manifest_commit",
    "diagnostics_report_commit",
)


def summarize_processing_reports(
    reports: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, int]]:
    """Summarize release stages for measured non-reset processing frames."""
    samples: dict[str, list[int]] = {stage: [] for stage in _PROCESSING_STAGES}
    samples["total_input_read"] = []
    samples["complete_frame"] = []
    for report in reports:
        for frame in report["frames"]:
            if frame["reset"]:
                continue
            stages = frame["stages_ns"]
            for stage in _PROCESSING_STAGES:
                if stage in stages:
                    samples[stage].append(int(stages[stage]))
            samples["total_input_read"].append(
                int(stages["beauty_read"] + stages["vector_read"] + stages["matte_read"])
            )
            samples["complete_frame"].append(int(frame["total_frame_ns"]))
    return {name: summarize_samples(tuple(values)) for name, values in samples.items()}


def summarize_samples(samples_ns: tuple[int, ...], *, frames_per_sample: int = 1) -> dict[str, int]:
    """Summarize positive-duration samples without imposing a performance threshold."""
    if not samples_ns:
        raise ValueError("at least one measured sample is required")
    if frames_per_sample < 1:
        raise ValueError("frames_per_sample must be positive")
    median_ns = int(statistics.median(samples_ns))
    return {
        "measured_count": len(samples_ns),
        "minimum_ns": min(samples_ns),
        "median_ns": median_ns,
        "maximum_ns": max(samples_ns),
        "extrapolated_147_frames_ns": median_ns * 147 // frames_per_sample,
    }
