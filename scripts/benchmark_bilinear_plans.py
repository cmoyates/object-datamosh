"""Benchmark reusable bilinear plans on deterministic 1080p Extreme input."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import platform
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
WIDTH = 1920
HEIGHT = 1080
SEED = 78078
REVISIONS = {
    "before": "b0f20d49cde073cc0d125121c36e23ec71218a47",
    "after": "796e8fda0c3fcd2e645138e3f5b0b7a8ab9a63da",
}
FEEDBACK_BLOBS = {
    "before": "461e40863c1db167d1627b05e12cd366962a4b78",
    "after": "b792af0d8aeeeddc3217e6be181147dcd6e2a7ff",
}


def _arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", required=True, choices=("before", "after"))
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--measured", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(values)
    if args.warmups < 1 or args.measured < 3:
        parser.error("--warmups must be positive and --measured must be at least 3")
    return args


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def _verify_provenance(args: argparse.Namespace) -> dict[str, str]:
    source = args.source_root.expanduser().resolve()
    if _git(source, "status", "--porcelain"):
        raise RuntimeError(f"source worktree must be clean: {source}")
    sha = _git(source, "rev-parse", "HEAD")
    feedback_blob = _git(source, "rev-parse", "HEAD:src/object_datamosh/core/feedback.py")
    disk_feedback_blob = _git(source, "hash-object", "src/object_datamosh/core/feedback.py")
    if sha != REVISIONS[args.revision] or feedback_blob != FEEDBACK_BLOBS[args.revision]:
        raise RuntimeError(f"source does not match pinned {args.revision} revision")
    if disk_feedback_blob != feedback_blob:
        raise RuntimeError("on-disk feedback.py does not match source revision")
    runner_blob = _git(SCRIPT_ROOT, "rev-parse", "HEAD:scripts/benchmark_bilinear_plans.py")
    disk_runner_blob = _git(SCRIPT_ROOT, "hash-object", str(Path(__file__).resolve()))
    if runner_blob != disk_runner_blob:
        raise RuntimeError("benchmark runner must match its committed Git blob")
    return {
        "sha": sha,
        "feedback_blob": feedback_blob,
        "runner_sha": _git(SCRIPT_ROOT, "rev-parse", "HEAD"),
        "runner_blob": runner_blob,
    }


def _summary(samples: list[int]) -> dict[str, int]:
    return {
        "measured_count": len(samples),
        "minimum_ns": min(samples),
        "median_ns": int(np.median(np.asarray(samples, dtype=np.int64))),
        "maximum_ns": max(samples),
    }


def _measure(function: Callable[[], object], warmups: int, measured: int) -> dict[str, int]:
    for _ in range(warmups):
        function()
    samples: list[int] = []
    for _ in range(measured):
        gc.collect()
        started = time.perf_counter_ns()
        function()
        samples.append(time.perf_counter_ns() - started)
    return _summary(samples)


def _digest_array(array: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(array).view(np.uint8)).hexdigest()


def _environment() -> dict[str, str]:
    try:
        import bpy

        blender = bpy.app.version_string
    except ImportError:
        blender = "unavailable"
    cpu = platform.processor() or platform.machine()
    if sys.platform == "darwin":
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
        cpu = result.stdout.strip() or cpu
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "blender": blender,
        "os": platform.platform(),
        "cpu": cpu,
    }


def _peak_rss_mib() -> float:
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024.0 if sys.platform != "darwin" else 1024.0 * 1024.0
    return peak / divisor


def main() -> None:
    args = _arguments()
    provenance = _verify_provenance(args)
    source = args.source_root.expanduser().resolve()
    sys.path.insert(0, str(source / "src"))

    from object_datamosh.core.contracts import FeedbackState
    from object_datamosh.core.feedback import process_frame_with_diagnostics
    from object_datamosh.core.presets import extreme_full_frame_feedback_settings
    from object_datamosh.core.sampling import bilinear_sample

    rng = np.random.default_rng(SEED)
    beauty = rng.random((HEIGHT, WIDTH, 4), dtype=np.float32).astype(np.float32, copy=False)
    history = rng.random((HEIGHT, WIDTH, 4), dtype=np.float32).astype(np.float32, copy=False)
    history_matte = rng.random((HEIGHT, WIDTH), dtype=np.float32).astype(np.float32, copy=False)
    matte = rng.random((HEIGHT, WIDTH), dtype=np.float32).astype(np.float32, copy=False)
    motion = rng.uniform(-2.0, 2.0, (HEIGHT, WIDTH, 4)).astype(np.float32)
    sample_y, sample_x = np.indices((HEIGHT, WIDTH), dtype=np.float32)
    sample_x += motion[..., 0]
    sample_y += motion[..., 1]
    settings = extreme_full_frame_feedback_settings()
    previous = FeedbackState(history, history_matte, 1)

    stages: dict[str, dict[str, int]] = {
        "repeated_rgba": _measure(
            lambda: bilinear_sample(history, sample_x, sample_y), args.warmups, args.measured
        ),
        "repeated_scalar": _measure(
            lambda: bilinear_sample(history_matte, sample_x, sample_y),
            args.warmups,
            args.measured,
        ),
        "repeated_total": _measure(
            lambda: (
                bilinear_sample(history, sample_x, sample_y),
                bilinear_sample(history_matte, sample_x, sample_y),
            ),
            args.warmups,
            args.measured,
        ),
    }
    allocation_proxy: dict[str, int] = {
        "coordinate_inputs_bytes": sample_x.nbytes + sample_y.nbytes,
        "retained_plan_bytes": 0,
    }
    if args.revision == "after":
        from object_datamosh.core.sampling import make_bilinear_plan, sample_with_plan

        plan = make_bilinear_plan(sample_x, sample_y, WIDTH, HEIGHT)
        stages.update(
            {
                "plan_construction": _measure(
                    lambda: make_bilinear_plan(sample_x, sample_y, WIDTH, HEIGHT),
                    args.warmups,
                    args.measured,
                ),
                "planned_rgba": _measure(
                    lambda: sample_with_plan(history, plan), args.warmups, args.measured
                ),
                "planned_scalar": _measure(
                    lambda: sample_with_plan(history_matte, plan), args.warmups, args.measured
                ),
                "planned_total": _measure(
                    lambda: (
                        lambda current: (
                            sample_with_plan(history, current),
                            sample_with_plan(history_matte, current),
                        )
                    )(make_bilinear_plan(sample_x, sample_y, WIDTH, HEIGHT)),
                    args.warmups,
                    args.measured,
                ),
            }
        )
        allocation_proxy["retained_plan_bytes"] = sum(
            getattr(plan, name).nbytes for name in ("valid", "x0", "x1", "y0", "y1", "wx", "wy")
        )

    def process() -> tuple[Any, ...]:
        return process_frame_with_diagnostics(
            beauty, motion, matte, previous, frame_number=2, settings=settings
        )

    full_feedback = _measure(process, args.warmups, args.measured)
    output, state, diagnostics = process()
    payload = {
        "schema_version": 1,
        "revision": args.revision,
        "source": provenance,
        "fixture": {
            "shape": [HEIGHT, WIDTH, 4],
            "dtype": "float32",
            "deterministic_seed": SEED,
            "preset": "extreme_full_frame_feedback_settings",
            "planned_samples": ["RGBA history", "scalar Trail coverage"],
        },
        "methodology": {
            "warmup_count": args.warmups,
            "measured_count": args.measured,
            "clock": "perf_counter_ns",
            "statistics": ["minimum", "median", "maximum"],
            "note": "Developer evidence, not a CI timing gate or a claim about other hardware.",
        },
        "environment": _environment(),
        "stages": stages,
        "full_feedback": full_feedback,
        "memory": {
            "process_peak_rss_mib": _peak_rss_mib(),
            "practical_allocation_proxy": allocation_proxy,
        },
        "semantic_digest": {
            "output": _digest_array(output),
            "history": _digest_array(state.history),
            "history_matte": _digest_array(state.history_matte),
            "diagnostics": hashlib.sha256(
                json.dumps(asdict(diagnostics), sort_keys=True).encode("utf-8")
            ).hexdigest(),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
