"""Reproducible 1080p Extreme-path benchmark; run with Blender's Python."""

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

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from object_datamosh.benchmarking import summarize_samples  # noqa: E402
from object_datamosh.blender_image_io import BlenderImageIO  # noqa: E402
from object_datamosh.core.block_preparation import prepare_blocks  # noqa: E402
from object_datamosh.core.contracts import FeedbackState  # noqa: E402
from object_datamosh.core.exr import _undo_zip_preprocessing  # noqa: E402
from object_datamosh.core.feedback import (  # noqa: E402
    _apply_refresh,
    process_frame_with_diagnostics,
)
from object_datamosh.core.mattes import ObjectIndexMatteProvider  # noqa: E402
from object_datamosh.core.paths import SequencePaths  # noqa: E402
from object_datamosh.core.presets import (  # noqa: E402
    extreme_full_frame_feedback_settings,
)
from object_datamosh.sequence_processing import (  # noqa: E402
    process_sequence,
    processing_report_path,
)

WIDTH = 1920
HEIGHT = 1080
SEQUENCE_FRAMES = 3
SEED = 71071


def _parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--measured", type=int, default=3)
    parser.add_argument("--output", type=Path)
    result = parser.parse_args(arguments)
    if result.warmups < 1 or result.measured < 1:
        parser.error("--warmups and --measured must both be positive")
    return result


def _fixtures() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(SEED)
    beauty = rng.random((HEIGHT, WIDTH, 4), dtype=np.float32)
    beauty[..., 3] = 1.0
    motion = np.zeros((HEIGHT, WIDTH, 4), dtype=np.float32)
    motion[..., 0] = np.float32(0.004)
    motion[..., 1] = np.float32(-0.003)
    matte = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
    matte[HEIGHT // 5 : HEIGHT * 4 // 5, WIDTH // 5 : WIDTH * 4 // 5] = 1.0
    history = np.roll(beauty, shift=(5, -7), axis=(0, 1)).copy()
    return beauty, motion, matte, history


def _measure(operation: Callable[[], object], warmups: int, measured: int) -> tuple[int, ...]:
    for _ in range(warmups):
        operation()
    samples: list[int] = []
    for _ in range(measured):
        started = time.perf_counter_ns()
        operation()
        samples.append(time.perf_counter_ns() - started)
    return tuple(samples)


def _summarize_throughput(samples: tuple[int, ...], bytes_per_sample: int) -> dict[str, int]:
    summary = summarize_samples(samples)
    summary["bytes_per_sample"] = bytes_per_sample
    summary["bytes_per_second"] = int(bytes_per_sample * 1_000_000_000 / summary["median_ns"])
    return summary


def _write_fixture_sequence(
    paths: SequencePaths,
    image_io: BlenderImageIO,
    beauty: np.ndarray,
    motion: np.ndarray,
    matte: np.ndarray,
) -> None:
    matte_rgba = np.repeat(matte[..., None], 4, axis=2).astype(np.float32, copy=False)
    for number in range(1, SEQUENCE_FRAMES + 1):
        frame = paths.frame(number)
        frame_beauty = np.roll(beauty, shift=number - 1, axis=1).copy()
        image_io.write_rgba(frame.beauty, frame_beauty)
        image_io.write_rgba(frame.vector, motion)
        image_io.write_rgba(frame.matte, matte_rgba)


def _environment() -> dict[str, str]:
    cpu = platform.processor() or platform.machine() or "unavailable"
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "blender": bpy.app.version_string,
        "os": platform.platform(),
        "cpu": cpu,
    }


def main() -> None:
    args = _parse_args()
    settings = extreme_full_frame_feedback_settings()
    beauty, motion, matte, history = _fixtures()
    state = FeedbackState(history, matte.copy(), 1)

    def pure_core() -> object:
        return process_frame_with_diagnostics(
            beauty, motion, matte, state, 2, settings, force_reset=False
        )

    core_samples = _measure(pure_core, args.warmups, args.measured)
    prepared_blocks = prepare_blocks(motion, matte, 2, settings)
    candidate = matte > 0.0
    covered = np.ones(matte.shape, dtype=bool)
    block_preparation_samples = _measure(
        lambda: prepare_blocks(motion, matte, 2, settings), args.warmups, args.measured
    )
    refresh_diagnostics_samples = _measure(
        lambda: _apply_refresh(
            prepared_blocks,
            candidate,
            covered,
            matte,
            settings.persistence,
        ),
        args.warmups,
        args.measured,
    )
    predictor_bytes = WIDTH * min(16, HEIGHT) * 4 * np.dtype(np.float32).itemsize
    predictor_fixture = (
        np.random.default_rng(SEED + 1)
        .integers(0, 256, size=predictor_bytes, dtype=np.uint8)
        .tobytes()
    )
    predictor_samples = _measure(
        lambda: _undo_zip_preprocessing(predictor_fixture), args.warmups, args.measured
    )
    with tempfile.TemporaryDirectory(prefix="ODM_extreme_benchmark_") as temporary:
        paths = SequencePaths(Path(temporary))
        image_io = BlenderImageIO(bpy.context.scene)
        _write_fixture_sequence(paths, image_io, beauty, motion, matte)
        frame = paths.frame(1)
        read_samples = {
            "beauty": _measure(
                lambda: image_io.read_rgba(frame.beauty), args.warmups, args.measured
            ),
            "vector": _measure(
                lambda: image_io.read_rgba(frame.vector), args.warmups, args.measured
            ),
            "matte": _measure(lambda: image_io.read_mask(frame.matte), args.warmups, args.measured),
            "all_three": _measure(
                lambda: (
                    image_io.read_rgba(frame.beauty),
                    image_io.read_rgba(frame.vector),
                    image_io.read_mask(frame.matte),
                ),
                args.warmups,
                args.measured,
            ),
        }
        write_samples = _measure(
            lambda: image_io.write_rgba(frame.processed, beauty),
            args.warmups,
            args.measured,
        )

        def complete_sequence() -> object:
            return process_sequence(
                paths,
                frame_start=1,
                frame_end=SEQUENCE_FRAMES,
                matte_provider=ObjectIndexMatteProvider(),
                settings=settings,
                image_io=image_io,
                overwrite=True,
            )

        end_to_end_samples = _measure(complete_sequence, args.warmups, args.measured)
        processing_report = json.loads(processing_report_path(paths).read_text(encoding="utf-8"))

    decoded_rgba_bytes = WIDTH * HEIGHT * 4 * np.dtype(np.float32).itemsize
    exr_read_bytes = {
        "beauty": decoded_rgba_bytes,
        "vector": decoded_rgba_bytes,
        "matte": decoded_rgba_bytes,
        "all_three": decoded_rgba_bytes * 3,
    }
    benchmarks: dict[str, Any] = {
        "zip_predictor_reversal": _summarize_throughput(predictor_samples, predictor_bytes),
        "block_preparation": summarize_samples(block_preparation_samples),
        "refresh_diagnostics": summarize_samples(refresh_diagnostics_samples),
        "pure_core_non_reset_frame": summarize_samples(core_samples),
        "exr_reads": {
            name: _summarize_throughput(samples, exr_read_bytes[name])
            for name, samples in read_samples.items()
        },
        "processed_exr_write": summarize_samples(write_samples),
        "complete_sequential_processing": summarize_samples(
            end_to_end_samples, frames_per_sample=SEQUENCE_FRAMES
        ),
    }
    comparable = {
        "block_preparation": benchmarks["block_preparation"]["median_ns"],
        "refresh_diagnostics": benchmarks["refresh_diagnostics"]["median_ns"],
        "pure_core_non_reset_frame": benchmarks["pure_core_non_reset_frame"]["median_ns"],
        "beauty_read": benchmarks["exr_reads"]["beauty"]["median_ns"],
        "vector_read": benchmarks["exr_reads"]["vector"]["median_ns"],
        "matte_read": benchmarks["exr_reads"]["matte"]["median_ns"],
        "processed_exr_write": benchmarks["processed_exr_write"]["median_ns"],
        "complete_sequential_processing_per_frame": (
            benchmarks["complete_sequential_processing"]["median_ns"] // SEQUENCE_FRAMES
        ),
    }
    largest = sorted(comparable.items(), key=lambda item: (-item[1], item[0]))
    payload = {
        "schema_version": 1,
        "fixture": {
            "width": WIDTH,
            "height": HEIGHT,
            "dtype": "float32",
            "channels": "RGBA",
            "sequence_frames": SEQUENCE_FRAMES,
            "deterministic_seed": SEED,
            "preset": "extreme_full_frame_feedback_settings",
        },
        "methodology": {
            "clock": "perf_counter_ns",
            "warmup_count": args.warmups,
            "measured_count": args.measured,
            "statistics": ["median", "minimum", "maximum"],
            "extrapolation_frames": 147,
            "threshold": None,
        },
        "environment": _environment(),
        "benchmarks": benchmarks,
        "largest_measured_stages": [
            {"stage": name, "median_ns": duration} for name, duration in largest
        ],
        "latest_processing_report_performance": processing_report["performance"],
        "semantic_result": "timing instrumentation is observational; correctness is gated by tests",
    }
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(serialized, end="")
    else:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized, encoding="utf-8")
        print(f"Wrote Extreme benchmark: {output}")


if __name__ == "__main__":
    main()
