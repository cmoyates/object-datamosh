import json
from pathlib import Path


def test_bilinear_plan_rejection_records_complete_benchmark_evidence() -> None:
    script = Path("scripts/benchmark_bilinear_plans.py").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")
    evidence = json.loads(Path("docs/evidence/issue-78-bilinear-plans.json").read_text())

    assert "HEIGHT = 1080" in script
    assert "WIDTH = 1920" in script
    assert "extreme_full_frame_feedback_settings" in script
    assert "benchmark runner must match its committed Git blob" in script
    assert "benchmark_bilinear_plans.py" in readme
    assert evidence["schema_version"] == 1
    assert evidence["issue"] == 78
    assert evidence["decision"] == "reject"
    assert evidence["fixture"]["shape"] == [1080, 1920, 4]
    assert evidence["methodology"]["warmup_count"] >= 1
    assert evidence["methodology"]["measured_count"] >= 3
    assert {"python", "numpy", "blender", "os", "cpu"} <= set(evidence["environment"])
    assert all(evidence["environment"].values())
    assert set(evidence["benchmarks"]["before"]["stages"]) == {
        "repeated_rgba",
        "repeated_scalar",
        "repeated_total",
    }
    assert {
        "repeated_rgba",
        "repeated_scalar",
        "repeated_total",
        "plan_construction",
        "planned_rgba",
        "planned_scalar",
        "planned_total",
    } == set(evidence["benchmarks"]["prototype"]["stages"])
    assert {
        "repeated_rgba",
        "repeated_scalar",
        "repeated_total",
    } <= set(evidence["benchmarks"]["prototype"]["stages"])
    assert evidence["comparison"]["retained_plan_bytes"] > 0
    assert evidence["comparison"]["peak_rss_growth_mib"] > 0
    assert evidence["semantic_comparison"]["bit_equal"] is True
    assert evidence["semantic_comparison"]["sampling_outputs_and_validity_bit_equal"] is True
    assert (
        evidence["semantic_comparison"]["feedback_output_state_coverage_and_diagnostics_bit_equal"]
        is True
    )
    assert evidence["semantic_comparison"]["maximum_absolute_error"] == 0.0
    revision_digests = evidence["semantic_comparison"]["digests_by_revision"]
    assert revision_digests["before"] == revision_digests["prototype"]
    complete_feedback = evidence["comparison"]["complete_feedback"]
    assert complete_feedback["reduction_percent"] < 5.0
    assert complete_feedback["estimated_147_frame_before_seconds"] == (
        complete_feedback["before_median_ns"] * 147 / 1_000_000_000
    )
    assert complete_feedback["estimated_147_frame_after_seconds"] == (
        complete_feedback["after_median_ns"] * 147 / 1_000_000_000
    )
    assert evidence["comparison"]["repeated_vs_planned_two_samples"]["reduction_percent"] < 0.0
    assert "planned samples 4.11% slower" in evidence["decision_reason"]
    assert "modest 2.79% gain" in readme
    assert "roadmap decision is to reject" in readme

    contract = evidence["prototype_contract_verification"]
    assert contract["source_sha"] == evidence["revisions"]["prototype"]["sha"]
    assert contract["result"].startswith("151 passed")
    assert {
        "compatibility wrapper",
        "scalar 2D and channel 3D reuse",
        "one-pixel image",
        "odd dimensions",
        "edge and fractional coordinates",
        "out-of-bounds coordinates",
        "NaN and infinite coordinates",
        "incompatible-dimension rejection",
        "feedback output, state, coverage, and diagnostics",
    } == set(contract["coverage"])


def test_rejected_bilinear_plan_does_not_leave_production_abstraction() -> None:
    sampling = Path("src/object_datamosh/core/sampling.py").read_text(encoding="utf-8")

    assert "make_bilinear_plan" not in sampling
    assert "sample_with_plan" not in sampling
