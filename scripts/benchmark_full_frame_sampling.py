"""Measure the Full Frame clean-history sampling path on deterministic 1080p data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
REVISION_SHAS = {
    "before": "0d98fb67fffd9b24cdd32ac053541268d6a25511",
    "after": "8220a56f4284969ca4f1270aad4fa64a76e926a5",
}
CORE_BLOBS = {
    "before": "839dc8e98c4987309eae8330d85f2e4cc20fda93",
    "after": "1db7511dbba9922aa651a17fb3b6afe223f99807",
}


def _requested_source_root() -> Path:
    """Read the source worktree early enough to control project imports."""
    try:
        value = sys.argv[sys.argv.index("--source-root") + 1]
    except (ValueError, IndexError):
        return SCRIPT_ROOT
    return Path(value).expanduser().resolve()


SOURCE_ROOT = _requested_source_root()
SRC = SOURCE_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from object_datamosh.benchmarking import summarize_samples  # noqa: E402
from object_datamosh.core.contracts import FeedbackState  # noqa: E402
from object_datamosh.core.feedback import process_frame_with_diagnostics  # noqa: E402
from object_datamosh.core.presets import (  # noqa: E402
    extreme_full_frame_feedback_settings,
)
from object_datamosh.core.sampling import bilinear_sample  # noqa: E402

WIDTH = 1920
HEIGHT = 1080
SEED = 75075


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--revision", choices=("before", "after"))
    mode.add_argument("--compare-before", type=Path)
    parser.add_argument("--compare-after", type=Path)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=SCRIPT_ROOT,
        help="Worktree whose core implementation is measured",
    )
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--measured", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.compare_before is not None:
        if args.compare_after is None:
            parser.error("--compare-after is required with --compare-before")
        return args
    if args.compare_after is not None:
        parser.error("--compare-after requires --compare-before")
    if args.warmups < 1 or args.measured < 3:
        parser.error("--warmups must be positive and --measured must be at least 3")
    source_root = args.source_root.expanduser().resolve()
    result = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    actual_sha = result.stdout.strip()
    blob_result = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD:src/object_datamosh/core/feedback.py"],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    actual_core_blob = blob_result.stdout.strip()
    expected_sha = REVISION_SHAS[args.revision]
    expected_core_blob = CORE_BLOBS[args.revision]
    status_result = subprocess.run(
        ["git", "-C", str(source_root), "status", "--porcelain"],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    disk_blob_result = subprocess.run(
        [
            "git",
            "-C",
            str(source_root),
            "hash-object",
            "src/object_datamosh/core/feedback.py",
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    disk_core_blob = disk_blob_result.stdout.strip()
    if result.returncode != 0 or blob_result.returncode != 0:
        parser.error(f"cannot resolve source revision in {source_root}")
    if status_result.returncode != 0 or status_result.stdout:
        parser.error(f"source worktree must be clean: {source_root}")
    if actual_sha != expected_sha:
        parser.error(
            f"--revision {args.revision} requires source HEAD {expected_sha}; got {actual_sha}"
        )
    if disk_blob_result.returncode != 0 or disk_core_blob != actual_core_blob:
        parser.error("on-disk feedback.py does not match the committed source revision")
    if actual_core_blob != expected_core_blob:
        parser.error(
            f"--revision {args.revision} requires feedback.py blob {expected_core_blob}; "
            f"got {actual_core_blob} at {actual_sha}"
        )
    if source_root != SOURCE_ROOT:
        parser.error("--source-root must be supplied before project imports")
    args.source_root = source_root
    args.source_sha = actual_sha
    args.source_core_blob = actual_core_blob
    return args


def _measure(operation: Callable[[], object], warmups: int, measured: int) -> dict[str, int]:
    for _ in range(warmups):
        operation()
    samples: list[int] = []
    for _ in range(measured):
        started = time.perf_counter_ns()
        operation()
        samples.append(time.perf_counter_ns() - started)
    return summarize_samples(tuple(samples))


def _fixtures() -> tuple[np.ndarray, np.ndarray, np.ndarray, FeedbackState]:
    rng = np.random.default_rng(SEED)
    beauty = rng.random((HEIGHT, WIDTH, 4), dtype=np.float32)
    beauty[..., 3] = 1.0
    motion = np.zeros_like(beauty)
    motion[..., 0] = np.float32(0.004)
    motion[..., 1] = np.float32(-0.003)
    matte = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
    matte[HEIGHT // 5 : HEIGHT * 4 // 5, WIDTH // 5 : WIDTH * 4 // 5] = 1.0
    history = np.roll(beauty, shift=(5, -7), axis=(0, 1)).astype(np.float32, copy=True)
    return beauty, motion, matte, FeedbackState(history, matte.copy(), 1)


def _blender_version() -> str:
    executable = os.environ.get("BLENDER_BIN")
    if not executable:
        return "unavailable (BLENDER_BIN not set)"
    result = subprocess.run(
        [executable, "--version"], capture_output=True, check=False, text=True, timeout=30
    )
    return result.stdout.splitlines()[0] if result.returncode == 0 else "unavailable"


def _environment() -> dict[str, str]:
    cpu = platform.processor() or platform.machine() or "unavailable"
    if sys.platform == "darwin":
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            check=False,
            text=True,
        )
        cpu = result.stdout.strip() or cpu
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "blender": _blender_version(),
        "os": platform.platform(),
        "cpu": cpu,
    }


def _digest(array: np.ndarray) -> str:
    return hashlib.sha256(array.tobytes()).hexdigest()


def _compare_results(before_path: Path, after_path: Path, output_path: Path | None) -> None:
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    if before.get("revision") != "before" or after.get("revision") != "after":
        raise ValueError("comparison inputs must be labeled before and after")
    for revision, payload in (("before", before), ("after", after)):
        source = payload.get("source", {})
        if source.get("sha") != REVISION_SHAS[revision]:
            raise ValueError(f"{revision} result does not identify the required source SHA")
        if source.get("feedback_blob") != CORE_BLOBS[revision]:
            raise ValueError(f"{revision} result does not identify the required feedback.py blob")
    if before.get("fixture") != after.get("fixture"):
        raise ValueError("comparison inputs must use identical fixtures")
    if before.get("environment") != after.get("environment"):
        raise ValueError("comparison inputs must come from the same environment")
    before_digests = before["semantic_digest"]
    after_digests = after["semantic_digest"]
    bit_equal = before_digests == after_digests
    comparison = {
        "schema_version": 1,
        "sources": {"before": before["source"], "after": after["source"]},
        "bit_equal": bit_equal,
        "maximum_absolute_error": 0.0 if bit_equal else None,
        "digests": after_digests
        if bit_equal
        else {"before": before_digests, "after": after_digests},
    }
    serialized = json.dumps(comparison, indent=2, sort_keys=True) + "\n"
    if output_path is None:
        print(serialized, end="")
    else:
        output_path.write_text(serialized, encoding="utf-8")


def main() -> None:
    args = _parse_args()
    if args.compare_before is not None:
        _compare_results(args.compare_before, args.compare_after, args.output)
        return
    warmups = args.warmups
    measured = args.measured
    beauty, motion, matte, state = _fixtures()
    settings = extreme_full_frame_feedback_settings()
    sample_y, sample_x = np.indices(matte.shape, dtype=np.float32)
    clean_valid = np.ones(matte.shape, dtype=bool)
    before_safe_history = np.where(clean_valid[..., None], state.history, 0.0)
    fallback_sample_x = sample_x.copy()
    fallback_sample_x[:, ::8] = -1.0
    representative_warped_history, representative_primary_covered = bilinear_sample(
        state.history, fallback_sample_x, sample_y
    )

    def before_primary() -> object:
        valid = np.all(np.isfinite(state.history), axis=-1)
        safe = np.where(valid[..., None], state.history, 0.0)
        return (
            bilinear_sample(safe, sample_x, sample_y),
            bilinear_sample((~valid).astype(np.float32), sample_x, sample_y),
        )

    def after_primary() -> object:
        valid = np.all(np.isfinite(state.history), axis=-1)
        return bool(np.all(valid)), bilinear_sample(state.history, sample_x, sample_y)

    def before_fallback() -> object:
        screen_y, screen_x = np.indices(matte.shape, dtype=np.float32)
        return (
            bilinear_sample(before_safe_history, screen_x, screen_y),
            bilinear_sample((~clean_valid).astype(np.float32), screen_x, screen_y),
        )

    def after_fallback() -> object:
        use_screen = ~representative_primary_covered & clean_valid
        return np.where(use_screen[..., None], state.history, representative_warped_history)

    def before_trail() -> object:
        valid = (
            np.isfinite(state.history_matte)
            & (state.history_matte >= 0.0)
            & (state.history_matte <= 1.0)
        )
        safe = np.where(valid, state.history_matte, 0.0).astype(np.float32, copy=False)
        return (
            bilinear_sample(safe, sample_x, sample_y),
            bilinear_sample((~valid).astype(np.float32), sample_x, sample_y),
        )

    def after_trail() -> object:
        valid = (
            np.isfinite(state.history_matte)
            & (state.history_matte >= 0.0)
            & (state.history_matte <= 1.0)
        )
        return bool(np.all(valid)), bilinear_sample(state.history_matte, sample_x, sample_y)

    benchmark_state = state
    benchmark_frame = 2

    def total_core_frame() -> object:
        nonlocal benchmark_frame, benchmark_state
        result = process_frame_with_diagnostics(
            beauty, motion, matte, benchmark_state, benchmark_frame, settings, force_reset=False
        )
        benchmark_state = result[1]
        benchmark_frame += 1
        return result

    before = args.revision == "before"
    operations: dict[str, Callable[[], object]] = {
        "coordinate_grid_allocation": (
            (lambda: np.indices(matte.shape, dtype=np.float32)) if before else (lambda: None)
        ),
        "primary_history_sampling": before_primary if before else after_primary,
        "same_pixel_fallback": before_fallback if before else after_fallback,
        "trail_mask_sampling": before_trail if before else after_trail,
        "total_core_frame": total_core_frame,
    }
    benchmarks = {
        name: _measure(operation, warmups, measured) for name, operation in operations.items()
    }
    semantic_state = state
    for semantic_frame in range(2, 6):
        output, semantic_state, diagnostics = process_frame_with_diagnostics(
            beauty,
            motion,
            matte,
            semantic_state,
            semantic_frame,
            settings,
            force_reset=False,
        )
    next_state = semantic_state
    payload: dict[str, Any] = {
        "schema_version": 1,
        "revision": args.revision,
        "source": {
            "root": str(args.source_root),
            "sha": args.source_sha,
            "feedback_blob": args.source_core_blob,
        },
        "fixture": {
            "shape": [HEIGHT, WIDTH, 4],
            "dtype": "float32",
            "deterministic_seed": SEED,
            "preset": "extreme_full_frame_feedback_settings",
        },
        "methodology": {
            "clock": "perf_counter_ns",
            "warmup_count": warmups,
            "measured_count": measured,
            "statistics": ["median", "minimum", "maximum"],
            "extrapolation_frames": 147,
        },
        "environment": _environment(),
        "benchmarks": benchmarks,
        "semantic_digest": {
            "output": _digest(output),
            "history": _digest(next_state.history),
            "history_matte": _digest(next_state.history_matte),
            "diagnostics": hashlib.sha256(
                json.dumps(asdict(diagnostics), sort_keys=True).encode()
            ).hexdigest(),
        },
    }
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")


if __name__ == "__main__":
    main()
