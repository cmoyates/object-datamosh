import json
from pathlib import Path

from object_datamosh.benchmarking import summarize_samples


def test_benchmark_summary_records_distribution_and_147_frame_extrapolation() -> None:
    summary = summarize_samples((3_000_000_000, 1_000_000_000, 2_000_000_000))

    assert summary == {
        "measured_count": 3,
        "minimum_ns": 1_000_000_000,
        "median_ns": 2_000_000_000,
        "maximum_ns": 3_000_000_000,
        "extrapolated_147_frames_ns": 294_000_000_000,
    }


def test_committed_benchmark_contract_uses_1080p_extreme_and_temporary_exrs() -> None:
    script = Path("scripts/benchmark_extreme.py").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "HEIGHT = 1080" in script
    assert "WIDTH = 1920" in script
    assert "extreme_full_frame_feedback_settings" in script
    assert "TemporaryDirectory" in script
    assert "process_frame_with_diagnostics" in script
    assert '"zip_predictor_reversal"' in script
    assert '"all_three"' in script
    assert '"bytes_per_second"' in script
    benchmark_command = (
        '"$BLENDER_BIN" --background --factory-startup --python scripts/benchmark_extreme.py'
    )
    assert benchmark_command in readme


def test_issue_73_evidence_reports_decode_throughput_before_and_after() -> None:
    evidence = json.loads(Path("docs/evidence/issue-73-exr-predictor.json").read_text())

    for revision in ("before", "after"):
        for pass_name in ("beauty", "vector", "matte", "all_three"):
            result = evidence["full_float_zip_decode"][revision][pass_name]
            assert result["bytes_per_sample"] > 0
            assert result["bytes_per_second"] > 0
            assert result["minimum_ns"] <= result["median_ns"] <= result["maximum_ns"]


def test_committed_baseline_has_separate_core_io_and_end_to_end_evidence() -> None:
    evidence = json.loads(Path("docs/evidence/extreme-benchmark-baseline.json").read_text())

    assert evidence["schema_version"] == 1
    assert evidence["fixture"] == {
        "width": 1920,
        "height": 1080,
        "dtype": "float32",
        "channels": "RGBA",
        "sequence_frames": 3,
        "deterministic_seed": 71071,
        "preset": "extreme_full_frame_feedback_settings",
    }
    assert evidence["methodology"]["warmup_count"] >= 1
    assert evidence["methodology"]["measured_count"] >= 1
    assert set(evidence["benchmarks"]) == {
        "pure_core_non_reset_frame",
        "exr_reads",
        "processed_exr_write",
        "complete_sequential_processing",
    }
    assert set(evidence["benchmarks"]["exr_reads"]) == {"beauty", "vector", "matte"}
    assert evidence["largest_measured_stages"]
    assert {"python", "numpy", "blender", "os", "cpu"} <= set(evidence["environment"])
