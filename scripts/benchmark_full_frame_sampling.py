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

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
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
    parser.add_argument("--revision", choices=("before", "after"), required=True)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--measured", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.warmups < 1 or args.measured < 3:
        parser.error("--warmups must be positive and --measured must be at least 3")
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


def main() -> None:
    args = _parse_args()
    warmups = args.warmups
    measured = args.measured
    beauty, motion, matte, state = _fixtures()
    settings = extreme_full_frame_feedback_settings()
    sample_y, sample_x = np.indices(matte.shape, dtype=np.float32)
    clean_valid = np.ones(matte.shape, dtype=bool)

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
            bilinear_sample(state.history, screen_x, screen_y),
            bilinear_sample((~clean_valid).astype(np.float32), screen_x, screen_y),
        )

    def after_fallback() -> object:
        use_screen = ~clean_valid & clean_valid
        return np.where(use_screen[..., None], state.history, state.history)

    def before_trail() -> object:
        return (
            bilinear_sample(state.history_matte, sample_x, sample_y),
            bilinear_sample((~clean_valid).astype(np.float32), sample_x, sample_y),
        )

    def after_trail() -> object:
        valid = (
            np.isfinite(state.history_matte)
            & (state.history_matte >= 0.0)
            & (state.history_matte <= 1.0)
        )
        return bool(np.all(valid)), bilinear_sample(state.history_matte, sample_x, sample_y)

    before = args.revision == "before"
    operations: dict[str, Callable[[], object]] = {
        "coordinate_grid_allocation": (
            (lambda: np.indices(matte.shape, dtype=np.float32)) if before else (lambda: None)
        ),
        "primary_history_sampling": before_primary if before else after_primary,
        "same_pixel_fallback": before_fallback if before else after_fallback,
        "trail_mask_sampling": before_trail if before else after_trail,
        "total_core_frame": lambda: process_frame_with_diagnostics(
            beauty, motion, matte, state, 2, settings, force_reset=False
        ),
    }
    benchmarks = {
        name: _measure(operation, warmups, measured) for name, operation in operations.items()
    }
    output, next_state, diagnostics = process_frame_with_diagnostics(
        beauty, motion, matte, state, 2, settings, force_reset=False
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "revision": args.revision,
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
