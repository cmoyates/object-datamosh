import json
import subprocess
import sys
from pathlib import Path


def test_full_frame_sampling_benchmark_records_required_before_after_evidence() -> None:
    script = Path("scripts/benchmark_full_frame_sampling.py").read_text(encoding="utf-8")
    evidence = json.loads(Path("docs/evidence/issue-75-full-frame-sampling.json").read_text())

    assert "extreme_full_frame_feedback_settings()" in script
    assert "HEIGHT = 1080" in script
    assert "WIDTH = 1920" in script
    assert "--source-root" in script
    assert "feedback.py blob" in script
    assert "~representative_primary_covered & clean_valid" in script
    assert "state.history, fallback_sample_x, sample_y" in script
    assert "state.history, representative_warped_history" in script
    assert "benchmark_state = result[1]" in script
    assert "benchmark runner must match its committed Git blob" in script
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
    assert evidence["revisions"]["before"]["sha"] == ("0d98fb67fffd9b24cdd32ac053541268d6a25511")
    assert evidence["revisions"]["after"]["sha"] == ("8220a56f4284969ca4f1270aad4fa64a76e926a5")


def test_full_frame_sampling_benchmark_rejects_invalid_source_provenance() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/benchmark_full_frame_sampling.py",
            "--revision",
            "before",
            "--source-root",
            ".",
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert (
        "benchmark runner must match its committed Git blob" in result.stderr
        or "source worktree must be clean" in result.stderr
        or "requires source HEAD" in result.stderr
        or "requires feedback.py blob" in result.stderr
    )


def test_full_frame_sampling_benchmark_compares_exact_semantic_digests(
    tmp_path: Path,
) -> None:
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    comparison = tmp_path / "comparison.json"
    digests = {"output": "a", "history": "b", "history_matte": "c", "diagnostics": "d"}
    fixture = {"shape": [1080, 1920, 4], "seed": 75075}
    before.write_text(
        json.dumps(
            {
                "revision": "before",
                "source": {
                    "sha": "0d98fb67fffd9b24cdd32ac053541268d6a25511",
                    "feedback_blob": "839dc8e98c4987309eae8330d85f2e4cc20fda93",
                },
                "fixture": fixture,
                "environment": {"cpu": "test"},
                "runner": {"sha": "runner", "blob": "runner-blob"},
                "semantic_digest": digests,
            }
        )
    )
    after.write_text(
        json.dumps(
            {
                "revision": "after",
                "source": {
                    "sha": "8220a56f4284969ca4f1270aad4fa64a76e926a5",
                    "feedback_blob": "1db7511dbba9922aa651a17fb3b6afe223f99807",
                },
                "fixture": fixture,
                "environment": {"cpu": "test"},
                "runner": {"sha": "runner", "blob": "runner-blob"},
                "semantic_digest": digests,
            }
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/benchmark_full_frame_sampling.py",
            "--compare-before",
            str(before),
            "--compare-after",
            str(after),
            "--output",
            str(comparison),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(comparison.read_text(encoding="utf-8"))
    assert payload["bit_equal"] is True
    assert payload["maximum_absolute_error"] == 0.0
    assert payload["digests"] == digests
