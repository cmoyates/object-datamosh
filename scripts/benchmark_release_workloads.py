"""Same-harness release workload benchmark for issue #79; run with Blender's Python.

This file intentionally uses only APIs present at the PERF-1 baseline so the exact script can be
copied into detached worktrees for directly comparable before/after runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from object_datamosh.blender_image_io import BlenderImageIO  # noqa: E402
from object_datamosh.core.contracts import (  # noqa: E402
    FeedbackMode,
    FeedbackSettings,
    FeedbackState,
)
from object_datamosh.core.feedback import process_frame_with_diagnostics  # noqa: E402
from object_datamosh.core.mattes import ObjectIndexMatteProvider  # noqa: E402
from object_datamosh.core.paths import SequencePaths  # noqa: E402
from object_datamosh.core.presets import (  # noqa: E402
    extreme_full_frame_feedback_settings,
)
from object_datamosh.sequence_processing import (  # noqa: E402
    SequenceRunMode,
    process_sequence,
    processing_report_path,
)

WIDTH = 1920
HEIGHT = 1080
SEED = 71071
EXTRAPOLATION_FRAMES = 147
STAGES = (
    "beauty_read",
    "vector_read",
    "matte_read",
    "total_input_read",
    "core_processing",
    "processed_exr_write",
    "manifest_commit",
    "diagnostics_report_commit",
    "complete_frame",
)


@dataclass(frozen=True)
class Workload:
    key: str
    label: str
    settings: FeedbackSettings
    first_matte: np.ndarray
    current_matte: np.ndarray
    history: np.ndarray
    history_matte: np.ndarray


def _parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--measured", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision-label", required=True)
    result = parser.parse_args(arguments)
    if result.warmups < 1 or result.measured < 1:
        parser.error("--warmups and --measured must both be positive")
    return result


def _summary(
    samples: Sequence[int], *, warmup_count: int, frames_per_sample: int = 1
) -> dict[str, int | list[int]]:
    if not samples:
        raise ValueError("at least one sample is required")
    median = int(statistics.median(samples))
    return {
        "warmup_count": warmup_count,
        "measured_count": len(samples),
        "samples_ns": list(samples),
        "minimum_ns": min(samples),
        "median_ns": median,
        "maximum_ns": max(samples),
        "extrapolated_147_frames_ns": median * EXTRAPOLATION_FRAMES // frames_per_sample,
    }


def _measure(operation: Callable[[], object], warmups: int, measured: int) -> tuple[int, ...]:
    for _ in range(warmups):
        operation()
    samples = []
    for _ in range(measured):
        started = time.perf_counter_ns()
        operation()
        samples.append(time.perf_counter_ns() - started)
    return tuple(samples)


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def _array_sha256(array: np.ndarray) -> str:
    """Hash an array's exact typed shape and C-order bytes for cross-revision comparison."""
    digest = hashlib.sha256()
    digest.update(array.dtype.str.encode("ascii"))
    digest.update(json.dumps(array.shape).encode("ascii"))
    digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def _semantic_result(result: Any) -> dict[str, object]:
    output, state, diagnostics = result
    return {
        "processed_rgba_sha256": _array_sha256(output),
        "next_history_rgba_sha256": _array_sha256(state.history),
        "next_history_matte_sha256": _array_sha256(state.history_matte),
        "next_frame_number": state.frame_number,
        "diagnostics": asdict(diagnostics),
    }


def _fixtures() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(SEED)
    beauty = rng.random((HEIGHT, WIDTH, 4), dtype=np.float32)
    beauty[..., 3] = 1.0
    motion = np.zeros((HEIGHT, WIDTH, 4), dtype=np.float32)
    motion[..., 0] = np.float32(0.004)
    motion[..., 1] = np.float32(-0.003)
    target = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
    target[HEIGHT // 5 : HEIGHT * 4 // 5, WIDTH // 5 : WIDTH * 4 // 5] = 1.0
    history = np.roll(beauty, shift=(5, -7), axis=(0, 1)).copy()
    return beauty, motion, target, history


def _workloads(beauty: np.ndarray, target: np.ndarray, history: np.ndarray) -> tuple[Workload, ...]:
    empty = np.zeros_like(target)
    extreme = extreme_full_frame_feedback_settings()
    return (
        Workload(
            "extreme_full_frame_trail",
            "Extreme Full Frame + Trail",
            extreme,
            target,
            target,
            history,
            target.copy(),
        ),
        Workload(
            "extreme_hard",
            "Extreme Hard",
            replace(extreme, mode=FeedbackMode.HARD_LOCALIZED),
            target,
            target,
            history,
            target.copy(),
        ),
        Workload(
            "target_only",
            "Target Only compatibility",
            FeedbackSettings(),
            target,
            target,
            history,
            target.copy(),
        ),
        Workload(
            "background_only_pre_roll",
            "background-only pre-roll",
            extreme,
            empty,
            target,
            history,
            empty.copy(),
        ),
        Workload(
            "nonzero_refresh",
            "nonzero refresh",
            replace(extreme, refresh_probability=0.25, seed=73079),
            target,
            target,
            history,
            target.copy(),
        ),
        Workload(
            "invalid_resumed_history",
            "invalid resumed history",
            extreme,
            target,
            target,
            history,
            target.copy(),
        ),
    )


def _write_sequence(
    paths: SequencePaths,
    image_io: BlenderImageIO,
    beauty: np.ndarray,
    motion: np.ndarray,
    workload: Workload,
) -> None:
    mattes = (workload.first_matte, workload.current_matte)
    for number, matte in enumerate(mattes, start=1):
        frame = paths.frame(number)
        frame_beauty = beauty if number == 2 else workload.history
        matte_rgba = np.repeat(matte[..., None], 4, axis=2).astype(np.float32, copy=False)
        image_io.write_rgba(frame.beauty, frame_beauty)
        image_io.write_rgba(frame.vector, motion)
        image_io.write_rgba(frame.matte, matte_rgba)


def _stage_summary(
    reports: Sequence[Mapping[str, Any]], *, warmup_count: int
) -> dict[str, dict[str, int | list[int]]]:
    samples: dict[str, list[int]] = {stage: [] for stage in STAGES}
    for report in reports:
        non_reset = [frame for frame in report["frames"] if not frame["reset"]]
        if len(non_reset) != 1:
            raise RuntimeError(f"expected one non-reset frame, got {len(non_reset)}")
        frame = non_reset[0]
        stages = frame["stages_ns"]
        for stage in STAGES:
            if stage == "total_input_read":
                value = stages["beauty_read"] + stages["vector_read"] + stages["matte_read"]
            elif stage == "complete_frame":
                value = frame["total_frame_ns"]
            else:
                value = stages[stage]
            samples[stage].append(int(value))
    return {stage: _summary(values, warmup_count=warmup_count) for stage, values in samples.items()}


def _git_revision() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def main() -> None:
    args = _parse_args()
    beauty, motion, target, history = _fixtures()
    workloads = _workloads(beauty, target, history)
    results: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="ODM_issue79_workloads_") as temporary:
        temporary_root = Path(temporary)
        image_io = BlenderImageIO(bpy.context.scene)
        for workload in workloads:
            state = FeedbackState(workload.history, workload.history_matte, 1)

            def pure_core(workload: Workload = workload, state: FeedbackState = state) -> object:
                return process_frame_with_diagnostics(
                    beauty,
                    motion,
                    workload.current_matte,
                    state,
                    2,
                    workload.settings,
                    force_reset=False,
                )

            paths = SequencePaths(temporary_root / workload.key)
            _write_sequence(paths, image_io, beauty, motion, workload)

            def complete_sequence(
                paths: SequencePaths = paths, workload: Workload = workload
            ) -> object:
                return process_sequence(
                    paths,
                    frame_start=1,
                    frame_end=2,
                    matte_provider=ObjectIndexMatteProvider(),
                    settings=workload.settings,
                    image_io=image_io,
                    overwrite=True,
                )

            definition = {
                "settings": {
                    "mode": workload.settings.mode.value,
                    "history_source": workload.settings.history_source.value,
                    "invalid_history_fallback": workload.settings.invalid_history_fallback.value,
                    "trail_decay": workload.settings.trail_decay,
                    "trail_motion_mix": workload.settings.trail_motion_mix,
                    "persistence": workload.settings.persistence,
                    "block_size": workload.settings.block_size,
                    "motion_channels": workload.settings.motion_channels.value,
                    "reverse_motion": workload.settings.reverse_motion,
                    "flip_x": workload.settings.flip_x,
                    "flip_y": workload.settings.flip_y,
                    "motion_gain": workload.settings.motion_gain,
                    "motion_clamp": workload.settings.motion_clamp,
                    "motion_quantization": workload.settings.motion_quantization,
                    "diffusion": workload.settings.diffusion,
                    "refresh_probability": workload.settings.refresh_probability,
                    "seed": workload.settings.seed,
                    "matte_source": workload.settings.matte_source.value,
                },
                "first_frame_target_pixels": int(np.count_nonzero(workload.first_matte)),
                "non_reset_target_pixels": int(np.count_nonzero(workload.current_matte)),
                "frames": 2,
            }
            if workload.key == "invalid_resumed_history":
                complete_sequence()
                invalid = np.ones((HEIGHT // 2, WIDTH // 2, 4), dtype=np.float32)
                image_io.write_rgba(paths.frame(2).processed, invalid)

                def reject_invalid_resume(
                    paths: SequencePaths = paths, workload: Workload = workload
                ) -> object:
                    try:
                        process_sequence(
                            paths,
                            frame_start=1,
                            frame_end=2,
                            matte_provider=ObjectIndexMatteProvider(),
                            settings=workload.settings,
                            image_io=image_io,
                            run_mode=SequenceRunMode.RESUME,
                        )
                    except RuntimeError as error:
                        if "Resume history is invalid" not in str(error):
                            raise
                        return error
                    raise AssertionError("invalid resumed history was accepted")

                rejection_samples = _measure(reject_invalid_resume, args.warmups, args.measured)
                definition["invalidity"] = (
                    "processed frame 2 is 960x540 while its matte and sequence are 1920x1080"
                )
                results[workload.key] = {
                    "label": workload.label,
                    "definition": definition,
                    "expected_rejection_end_to_end": _summary(
                        rejection_samples, warmup_count=args.warmups
                    ),
                    "stage_availability": (
                        "No core or EXR-write stages run: resume validation rejects the "
                        "malformed history before frame processing by design."
                    ),
                }
                continue

            semantic_result = _semantic_result(pure_core())
            pure_core_samples = _measure(pure_core, args.warmups, args.measured)
            # Prime overwrite/recovery paths once after their configured warm-ups. This run is
            # deliberately excluded from both elapsed and production-stage sample distributions.
            _measure(complete_sequence, args.warmups, 1)
            end_to_end_samples: list[int] = []
            reports: list[dict[str, Any]] = []
            for _ in range(args.measured):
                started = time.perf_counter_ns()
                complete_sequence()
                end_to_end_samples.append(time.perf_counter_ns() - started)
                reports.append(
                    json.loads(processing_report_path(paths).read_text(encoding="utf-8"))[
                        "performance"
                    ]
                )
            results[workload.key] = {
                "label": workload.label,
                "definition": definition,
                "semantic_non_reset_frame": semantic_result,
                "pure_core_non_reset_frame": _summary(pure_core_samples, warmup_count=args.warmups),
                "exr_io_and_release_stages_non_reset_frame": _stage_summary(
                    reports, warmup_count=args.warmups
                ),
                "end_to_end_two_frame_sequence": _summary(
                    end_to_end_samples, warmup_count=args.warmups, frames_per_sample=2
                ),
            }

    representative_arrays = (beauty, motion, target, history, target.copy())
    payload = {
        "schema_version": 2,
        "revision": {"label": args.revision_label, "commit": _git_revision()},
        "fixture": {
            "width": WIDTH,
            "height": HEIGHT,
            "dtype": "float32",
            "channels": "RGBA",
            "deterministic_seed": SEED,
            "workload_order": [workload.key for workload in workloads],
        },
        "methodology": {
            "harness": "scripts/benchmark_release_workloads.py",
            "clock": "perf_counter_ns",
            "warmup_count_per_operation": args.warmups,
            "sequence_priming_runs_after_warmups": 1,
            "measured_count_per_operation": args.measured,
            "statistics": ["minimum", "median", "maximum"],
            "extrapolation_frames": EXTRAPOLATION_FRAMES,
        },
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "blender": bpy.app.version_string,
            "blender_build_hash": bpy.app.build_hash.decode(),
            "os": platform.platform(),
            "cpu": platform.processor() or platform.machine() or "unavailable",
        },
        "workloads": results,
        "memory": {
            "representative_live_array_definition": (
                "beauty RGBA + Vector RGBA + matte + history RGBA + history matte, "
                "all 1920x1080 float32"
            ),
            "representative_live_array_bytes": sum(array.nbytes for array in representative_arrays),
            "process_peak_rss_bytes": _peak_rss_bytes(),
            "measurement_scope": (
                "isolated Blender benchmark process peak after all workloads in recorded "
                "workload_order"
            ),
        },
        "comparability": {
            "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "same_harness_bytes": True,
            "same_machine_required": True,
            "production_code_varies_by_revision": True,
            "limits": (
                "Process peak RSS includes Blender, fixture creation, allocator high-water "
                "marks, EXR buffers, and all workloads; it is not per-stage or incremental "
                "allocation."
            ),
        },
    }
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote issue #79 workload benchmark: {output}")


if __name__ == "__main__":
    main()
