"""Small dependency-free helpers for reproducible developer benchmark reports."""

import statistics


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
