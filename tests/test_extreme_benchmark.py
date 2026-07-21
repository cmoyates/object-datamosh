import json
from pathlib import Path

from object_datamosh.benchmarking import summarize_processing_reports, summarize_samples


def test_benchmark_summary_records_distribution_and_147_frame_extrapolation() -> None:
    summary = summarize_samples((3_000_000_000, 1_000_000_000, 2_000_000_000))

    assert summary == {
        "measured_count": 3,
        "minimum_ns": 1_000_000_000,
        "median_ns": 2_000_000_000,
        "maximum_ns": 3_000_000_000,
        "extrapolated_147_frames_ns": 294_000_000_000,
    }


def test_processing_report_summary_covers_release_stages_and_non_reset_frames() -> None:
    reports = (
        {
            "frames": [
                {
                    "reset": True,
                    "stages_ns": {
                        "beauty_read": 1,
                        "vector_read": 2,
                        "matte_read": 3,
                        "core_processing": 4,
                        "processed_exr_write": 5,
                        "manifest_commit": 6,
                        "diagnostics_report_commit": 7,
                    },
                    "total_frame_ns": 28,
                },
                {
                    "reset": False,
                    "stages_ns": {
                        "beauty_read": 10,
                        "vector_read": 20,
                        "matte_read": 30,
                        "core_processing": 40,
                        "processed_exr_write": 50,
                        "manifest_commit": 60,
                    },
                    "total_frame_ns": 280,
                },
            ]
        },
        {
            "frames": [
                {
                    "reset": False,
                    "stages_ns": {
                        "beauty_read": 12,
                        "vector_read": 22,
                        "matte_read": 32,
                        "core_processing": 42,
                        "processed_exr_write": 52,
                        "manifest_commit": 62,
                        "diagnostics_report_commit": 72,
                    },
                    "total_frame_ns": 294,
                }
            ]
        },
    )

    result = summarize_processing_reports(reports)

    assert result["beauty_read"]["median_ns"] == 11
    assert result["total_input_read"]["minimum_ns"] == 60
    assert result["total_input_read"]["maximum_ns"] == 66
    assert result["complete_frame"]["median_ns"] == 287
    assert result["complete_frame"]["measured_count"] == 2
    assert set(result) == {
        "beauty_read",
        "vector_read",
        "matte_read",
        "total_input_read",
        "core_processing",
        "processed_exr_write",
        "manifest_commit",
        "diagnostics_report_commit",
        "complete_frame",
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
    assert '"bundled_exr_decodes"' in script
    assert '"custom_reader_first"' in script
    assert '"blender_probe_first"' in script
    assert '"blender_data_block_overhead_ns"' in script
    assert '"temporary_data_block_count"' in script
    assert '"all_three"' in script
    assert '"bytes_per_second"' in script
    assert '"release_stage_timings"' in script
    assert '"memory"' in script
    assert "summarize_processing_reports" in script
    assert 'output.format.file_format = "OPEN_EXR_MULTILAYER"' in script
    assert 'read_full_float_rgba(read_fixtures["beauty"])' in script
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
