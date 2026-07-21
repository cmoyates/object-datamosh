import json
from pathlib import Path


def test_full_frame_sampling_benchmark_records_required_before_after_evidence() -> None:
    script = Path("scripts/benchmark_full_frame_sampling.py").read_text(encoding="utf-8")
    evidence = json.loads(Path("docs/evidence/issue-75-full-frame-sampling.json").read_text())

    assert "extreme_full_frame_feedback_settings()" in script
    assert "HEIGHT = 1080" in script
    assert "WIDTH = 1920" in script
    assert evidence["schema_version"] == 1
    assert evidence["fixture"]["shape"] == [1080, 1920, 4]
    assert evidence["fixture"]["preset"] == "extreme_full_frame_feedback_settings"
    assert evidence["methodology"]["warmup_count"] >= 1
    assert evidence["methodology"]["measured_count"] >= 3
    assert {"python", "numpy", "blender", "os", "cpu"} <= set(evidence["environment"])
    required = {
        "coordinate_grid_allocation",
        "primary_history_sampling",
        "same_pixel_fallback",
        "trail_mask_sampling",
        "total_core_frame",
    }
    assert set(evidence["benchmarks"]["before"]) == required
    assert set(evidence["benchmarks"]["after"]) == required
    for revision in ("before", "after"):
        for stage in required:
            summary = evidence["benchmarks"][revision][stage]
            assert summary["measured_count"] >= 3
            assert summary["minimum_ns"] <= summary["median_ns"] <= summary["maximum_ns"]
    assert (
        evidence["benchmarks"]["after"]["total_core_frame"]["median_ns"]
        < evidence["benchmarks"]["before"]["total_core_frame"]["median_ns"]
    )
    assert evidence["semantic_comparison"]["bit_equal"] is True
    assert evidence["semantic_comparison"]["maximum_absolute_error"] == 0.0
