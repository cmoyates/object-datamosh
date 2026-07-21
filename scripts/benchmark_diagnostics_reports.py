"""Benchmark diagnostics-report checkpointing with a deterministic 147-frame sequence."""

from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import numpy as np  # noqa: E402

from object_datamosh.benchmarking import summarize_samples  # noqa: E402
from object_datamosh.core.diagnostics import (  # noqa: E402
    FrameDiagnostics,
    ProcessingDiagnostics,
)
from object_datamosh.sequence_processing import _write_json_atomic  # noqa: E402

FRAME_COUNT = 147
CHECKPOINT_INTERVAL = 10


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--measured", type=int, default=7)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/evidence/issue-74-diagnostics-checkpoint.json"),
    )
    args = parser.parse_args()
    if args.warmups < 1 or args.measured < 1:
        parser.error("--warmups and --measured must both be positive")
    return args


def _frame(number: int) -> FrameDiagnostics:
    pixels = 1920 * 1080
    return FrameDiagnostics(
        frame_number=number,
        reset=number == 1,
        pixel_count=pixels,
        target_matte_pixels=pixels // 3,
        target_matte_coverage=1.0 / 3.0,
        effect_matte_pixels=pixels // 3,
        effect_matte_coverage=1.0 / 3.0,
        primary_history_attempts=0 if number == 1 else pixels // 3,
        primary_history_valid_uses=0 if number == 1 else pixels // 3,
        primary_history_invalid_samples=0,
        same_pixel_fallback_attempts=0,
        same_pixel_fallback_valid_uses=0,
        current_beauty_fallback_pixels=0,
        refresh_restored_pixels=0,
        refresh_restored_blocks=0,
        historical_blend_pixels=0 if number == 1 else pixels // 3,
        historical_blend_weight=0.0 if number == 1 else float(pixels // 3) * 0.85,
        changed_output_pixels=0 if number == 1 else pixels // 3,
        changed_output_ratio=0.0 if number == 1 else 1.0 / 3.0,
        changed_output_mean_absolute=0.0 if number == 1 else 0.05,
        changed_output_max_absolute=0.0 if number == 1 else 0.2,
    )


def _write_prefixes(strategy: str) -> tuple[int, ...]:
    if strategy == "before":
        return (0, *(number for number in range(1, FRAME_COUNT + 1) for _ in range(2)))
    checkpoints = range(CHECKPOINT_INTERVAL, FRAME_COUNT, CHECKPOINT_INTERVAL)
    return (0, *(number for number in checkpoints for _ in range(2)), FRAME_COUNT, FRAME_COUNT)


def _payload(frames: tuple[FrameDiagnostics, ...], prefix: int) -> dict[str, object]:
    completed = tuple(range(1, prefix + 1))
    return ProcessingDiagnostics.from_frames(frames[:prefix]).to_report_payload(
        outcome="SUCCESS" if prefix == FRAME_COUNT else "RUNNING",
        frame_start=1,
        frame_end=FRAME_COUNT,
        completed_frames=completed,
        configuration={"history_source": "FULL_FRAME"},
        manifest_path="ODM_sequence_manifest.json",
        report_path="ODM_processing_report.json",
        settings_fingerprint="benchmark",
        checkpoint_interval_frames=CHECKPOINT_INTERVAL,
        active_report_may_lag_manifest=prefix < FRAME_COUNT,
    )


def _measure(operation: Callable[[], object], warmups: int, measured: int) -> tuple[int, ...]:
    for _ in range(warmups):
        operation()
    samples: list[int] = []
    for _ in range(measured):
        started = time.perf_counter_ns()
        operation()
        samples.append(time.perf_counter_ns() - started)
    return tuple(samples)


def _benchmark_strategy(
    strategy: str,
    frames: tuple[FrameDiagnostics, ...],
    directory: Path,
    warmups: int,
    measured: int,
) -> dict[str, Any]:
    prefixes = _write_prefixes(strategy)
    payloads = tuple(_payload(frames, prefix) for prefix in prefixes)
    destination = directory / f"{strategy}.json"

    construction_samples = _measure(
        lambda: tuple(
            json.dumps(_payload(frames, prefix), sort_keys=True, separators=(",", ":"))
            for prefix in prefixes
        ),
        warmups,
        measured,
    )
    write_samples = _measure(
        lambda: tuple(
            _write_json_atomic(destination, payload, compact=True) for payload in payloads
        ),
        warmups,
        measured,
    )

    def sequence() -> None:
        for prefix in prefixes:
            _write_json_atomic(destination, _payload(frames, prefix), compact=True)

    sequence_samples = _measure(sequence, warmups, measured)
    return {
        "json_construction": summarize_samples(construction_samples, frames_per_sample=FRAME_COUNT),
        "atomic_report_writes": {
            **summarize_samples(write_samples, frames_per_sample=FRAME_COUNT),
            "write_count": len(prefixes),
        },
        "sequence_overhead": summarize_samples(sequence_samples, frames_per_sample=FRAME_COUNT),
    }


def main() -> None:
    args = _parse_args()
    frames = tuple(_frame(number) for number in range(1, FRAME_COUNT + 1))
    with tempfile.TemporaryDirectory(prefix="ODM_diagnostics_benchmark_") as temporary:
        directory = Path(temporary)
        before = _benchmark_strategy("before", frames, directory, args.warmups, args.measured)
        after = _benchmark_strategy("after", frames, directory, args.warmups, args.measured)

    before_writes = before["atomic_report_writes"]["write_count"]
    after_writes = after["atomic_report_writes"]["write_count"]
    report: dict[str, object] = {
        "schema_version": 1,
        "fixture": {
            "frame_count": FRAME_COUNT,
            "resolution": [1920, 1080],
            "checkpoint_interval_frames": CHECKPOINT_INTERVAL,
            "bounded_detailed_frame_limit": 96,
        },
        "methodology": {
            "warmup_count": args.warmups,
            "measured_count": args.measured,
            "clock": "perf_counter_ns",
            "statistics": "median with minimum and maximum",
            "temporary_outputs": True,
        },
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "os": platform.platform(),
            "cpu": platform.processor() or platform.machine() or "unavailable",
        },
        "benchmarks": {"before": before, "after": after},
        "decision": {
            "before_write_count": before_writes,
            "after_write_count": after_writes,
            "report_write_reduction_percent": round(
                (before_writes - after_writes) * 100.0 / before_writes, 2
            ),
            "recovery_manifest_cadence": "unchanged: one atomic commit per completed frame",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
