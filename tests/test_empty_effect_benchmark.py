import json
from pathlib import Path


def test_empty_effect_benchmark_records_required_before_after_evidence() -> None:
    script = Path("scripts/benchmark_empty_effect_frames.py").read_text(encoding="utf-8")
    evidence = json.loads(Path("docs/evidence/issue-77-empty-effect-frames.json").read_text())

    assert "HEIGHT = 1080" in script
    assert "WIDTH = 1920" in script
    assert "extreme_full_frame_feedback_settings()" in script
    assert "--source-root" in script
    assert "feedback.py blob" in script
    assert "benchmark runner must match its committed Git blob" in script
    assert evidence["schema_version"] == 1
    assert evidence["fixture"]["shape"] == [1080, 1920, 4]
    assert evidence["fixture"]["preset"] == "extreme_full_frame_feedback_settings"
    assert evidence["methodology"]["warmup_count"] >= 1
    assert evidence["methodology"]["measured_count"] >= 3
    assert {"python", "numpy", "blender", "os", "cpu"} <= set(evidence["environment"])
    required = {
        "empty_hard_core",
        "empty_trail_core",
        "preroll_30_core",
        "preroll_60_core",
        "mixed_perf_1_core",
        "empty_hard_end_to_end",
        "empty_trail_end_to_end",
        "preroll_30_end_to_end",
        "preroll_60_end_to_end",
        "mixed_perf_1_end_to_end",
    }
    assert set(evidence["benchmarks"]["before"]) == required
    assert set(evidence["benchmarks"]["after"]) == required
    for revision in ("before", "after"):
        for workload in required:
            summary = evidence["benchmarks"][revision][workload]
            assert summary["measured_count"] >= 3
            assert summary["minimum_ns"] <= summary["median_ns"] <= summary["maximum_ns"]
    assert evidence["semantic_comparison"]["bit_equal"] is True
    assert evidence["semantic_comparison"]["maximum_absolute_error"] == 0.0
    assert (
        evidence["benchmarks"]["after"]["preroll_60_core"]["median_ns"]
        < evidence["benchmarks"]["before"]["preroll_60_core"]["median_ns"]
    )
