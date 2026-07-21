import json
from pathlib import Path


def test_diagnostics_checkpoint_benchmark_records_147_frame_before_after_evidence() -> None:
    script = Path("scripts/benchmark_diagnostics_reports.py").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")
    evidence = json.loads(
        Path("docs/evidence/issue-74-diagnostics-checkpoint.json").read_text(encoding="utf-8")
    )

    assert "FRAME_COUNT = 147" in script
    assert "ProcessingDiagnostics.from_frames" in script
    assert "_write_json_atomic" in script
    assert "uv run python scripts/benchmark_diagnostics_reports.py" in readme
    assert evidence["schema_version"] == 1
    assert evidence["fixture"]["frame_count"] == 147
    assert evidence["methodology"]["warmup_count"] >= 1
    assert evidence["methodology"]["measured_count"] >= 1
    for revision in ("before", "after"):
        result = evidence["benchmarks"][revision]
        assert (
            result["json_construction"]["minimum_ns"]
            <= result["json_construction"]["median_ns"]
            <= result["json_construction"]["maximum_ns"]
        )
        assert result["atomic_report_writes"]["write_count"] > 0
        assert result["sequence_overhead"]["median_ns"] > 0
    assert (
        evidence["benchmarks"]["after"]["atomic_report_writes"]["write_count"]
        < evidence["benchmarks"]["before"]["atomic_report_writes"]["write_count"]
    )
    assert evidence["decision"]["report_write_reduction_percent"] > 80
