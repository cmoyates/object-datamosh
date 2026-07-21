"""Measure empty-effect fast paths on deterministic 1080p recursive workloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
REVISION_SHAS = {
    "before": "ad39b77625741399730ce78678b64d7b24eee64f",
    "after": "d18e48aa9283a2039d87a7f2f4e21ac5b304d715",
}
CORE_BLOBS = {
    "before": "1db7511dbba9922aa651a17fb3b6afe223f99807",
    "after": "6aa5bc09896dda8011b9ca319208822977686b85",
}
WIDTH = 1920
HEIGHT = 1080
SEED = 77077


def _requested_source_root() -> Path:
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
from object_datamosh.core.contracts import (  # noqa: E402
    FeedbackMode,
    FeedbackState,
)
from object_datamosh.core.feedback import process_frame_with_diagnostics  # noqa: E402
from object_datamosh.core.mattes import ObjectIndexMatteProvider  # noqa: E402
from object_datamosh.core.paths import SequencePaths  # noqa: E402
from object_datamosh.core.presets import (  # noqa: E402
    extreme_full_frame_feedback_settings,
)
from object_datamosh.sequence_processing import process_sequence  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--revision", choices=("before", "after"))
    mode.add_argument("--compare-before", type=Path)
    parser.add_argument("--compare-after", type=Path)
    parser.add_argument("--source-root", type=Path, default=SCRIPT_ROOT)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--measured", type=int, default=3)
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
    args.runner_sha, args.runner_blob = _validate_runner(parser)
    source_root = args.source_root.expanduser().resolve()
    source_sha, source_blob = _validate_source(parser, source_root, args.revision)
    if source_root != SOURCE_ROOT:
        parser.error("--source-root must be supplied before project imports")
    args.source_root = source_root
    args.source_sha = source_sha
    args.source_blob = source_blob
    return args


def _git(*arguments: str, root: Path = SCRIPT_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )


def _validate_runner(parser: argparse.ArgumentParser) -> tuple[str, str]:
    sha = _git("rev-parse", "HEAD")
    blob = _git("rev-parse", "HEAD:scripts/benchmark_empty_effect_frames.py")
    disk = _git("hash-object", str(Path(__file__).resolve()))
    if (
        sha.returncode != 0
        or blob.returncode != 0
        or disk.returncode != 0
        or disk.stdout.strip() != blob.stdout.strip()
    ):
        parser.error("benchmark runner must match its committed Git blob")
    return sha.stdout.strip(), blob.stdout.strip()


def _validate_source(
    parser: argparse.ArgumentParser, source_root: Path, revision: str
) -> tuple[str, str]:
    sha = _git("rev-parse", "HEAD", root=source_root)
    blob = _git("rev-parse", "HEAD:src/object_datamosh/core/feedback.py", root=source_root)
    status = _git("status", "--porcelain", root=source_root)
    disk = _git("hash-object", "src/object_datamosh/core/feedback.py", root=source_root)
    actual_sha = sha.stdout.strip()
    actual_blob = blob.stdout.strip()
    if sha.returncode != 0 or blob.returncode != 0:
        parser.error(f"cannot resolve source revision in {source_root}")
    if status.returncode != 0 or status.stdout:
        parser.error(f"source worktree must be clean: {source_root}")
    if actual_sha != REVISION_SHAS[revision]:
        parser.error(f"--revision {revision} requires source HEAD {REVISION_SHAS[revision]}")
    if disk.returncode != 0 or disk.stdout.strip() != actual_blob:
        parser.error("on-disk feedback.py does not match the committed source revision")
    if actual_blob != CORE_BLOBS[revision]:
        parser.error(f"--revision {revision} requires feedback.py blob {CORE_BLOBS[revision]}")
    return actual_sha, actual_blob


def _measure(operation: Callable[[], object], warmups: int, measured: int) -> dict[str, int]:
    for _ in range(warmups):
        operation()
    samples: list[int] = []
    for _ in range(measured):
        started = time.perf_counter_ns()
        operation()
        samples.append(time.perf_counter_ns() - started)
    return summarize_samples(tuple(samples))


def _fixtures() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(SEED)
    beauty = rng.random((HEIGHT, WIDTH, 4), dtype=np.float32)
    beauty[..., 3] = 1.0
    history = np.roll(beauty, shift=(5, -7), axis=(0, 1)).copy()
    motion = np.zeros_like(beauty)
    empty = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
    return beauty, history, motion, empty


def _settings(mode: FeedbackMode) -> Any:
    settings = extreme_full_frame_feedback_settings()
    return type(settings)(**{**asdict(settings), "mode": mode})


def _run_core(
    frame_count: int,
    mode: FeedbackMode,
    beauty: np.ndarray,
    history: np.ndarray,
    motion: np.ndarray,
    matte: np.ndarray,
) -> tuple[np.ndarray, FeedbackState, Any]:
    prior_matte = np.zeros_like(matte) if mode is FeedbackMode.TRAIL else np.ones_like(matte)
    state = FeedbackState(history, prior_matte, 0)
    result: tuple[np.ndarray, FeedbackState, Any] | None = None
    settings = _settings(mode)
    for frame_number in range(1, frame_count + 1):
        result = process_frame_with_diagnostics(
            beauty, motion, matte, state, frame_number, settings
        )
        state = result[1]
    assert result is not None
    return result


def _run_mixed(
    beauty: np.ndarray, history: np.ndarray, motion: np.ndarray, matte: np.ndarray
) -> tuple[np.ndarray, FeedbackState, Any]:
    mixed_matte = matte.copy()
    mixed_matte[HEIGHT // 5 : HEIGHT * 4 // 5, WIDTH // 5 : WIDTH * 4 // 5] = 1.0
    return _run_core(3, FeedbackMode.TRAIL, beauty, history, motion, mixed_matte)


class _BenchmarkImageIO:
    def __init__(self, beauty: np.ndarray, motion: np.ndarray, matte: np.ndarray) -> None:
        self.beauty = beauty
        self.motion = motion
        self.matte = matte

    def read_rgba(self, path: Path) -> np.ndarray:
        if path.parent.name == "beauty":
            return self.beauty.copy()
        if path.parent.name == "vector":
            return self.motion.copy()
        raise AssertionError(f"unexpected RGBA read: {path}")

    def read_mask(self, path: Path) -> np.ndarray:
        if path.parent.name != "matte":
            raise AssertionError(f"unexpected mask read: {path}")
        return self.matte.copy()

    def write_rgba(self, path: Path, _pixels: np.ndarray) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def _run_end_to_end(
    eligible_frames: int,
    mode: FeedbackMode,
    beauty: np.ndarray,
    motion: np.ndarray,
    matte: np.ndarray,
) -> object:
    with tempfile.TemporaryDirectory(prefix="ODM_issue_77_benchmark_") as temporary:
        paths = SequencePaths(Path(temporary))
        return process_sequence(
            paths,
            frame_start=1,
            frame_end=eligible_frames + 1,
            matte_provider=ObjectIndexMatteProvider(),
            settings=_settings(mode),
            image_io=_BenchmarkImageIO(beauty, motion, matte),
            overwrite=True,
        )


def _digest(result: tuple[np.ndarray, FeedbackState, Any]) -> str:
    output, state, diagnostics = result
    digest = hashlib.sha256()
    digest.update(output.tobytes())
    digest.update(state.history.tobytes())
    digest.update(state.history_matte.tobytes())
    digest.update(json.dumps(asdict(diagnostics), sort_keys=True).encode())
    return digest.hexdigest()


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
    blender = "unavailable (BLENDER_BIN not set)"
    if executable := os.environ.get("BLENDER_BIN"):
        result = subprocess.run(
            [executable, "--version"], capture_output=True, check=False, text=True, timeout=30
        )
        if result.returncode == 0:
            blender = result.stdout.splitlines()[0]
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "blender": blender,
        "os": platform.platform(),
        "cpu": cpu,
    }


def _compare(before_path: Path, after_path: Path, output: Path | None) -> None:
    before = json.loads(before_path.read_text())
    after = json.loads(after_path.read_text())
    for revision, payload in (("before", before), ("after", after)):
        if payload["source"]["sha"] != REVISION_SHAS[revision]:
            raise ValueError(f"{revision} result has the wrong source SHA")
        if payload["source"]["feedback_blob"] != CORE_BLOBS[revision]:
            raise ValueError(f"{revision} result has the wrong feedback.py blob")
    if before["fixture"] != after["fixture"] or before["environment"] != after["environment"]:
        raise ValueError("comparison inputs must use identical fixtures and environment")
    if before["runner"] != after["runner"]:
        raise ValueError("comparison inputs must use the same committed runner")
    bit_equal = before["semantic_digests"] == after["semantic_digests"]
    benchmarks = {"before": before["benchmarks"], "after": after["benchmarks"]}
    comparisons = {}
    for name, before_summary in before["benchmarks"].items():
        before_ns = before_summary["median_ns"]
        after_ns = after["benchmarks"][name]["median_ns"]
        comparisons[name] = {
            "median_reduction_percent": round((before_ns - after_ns) * 100 / before_ns, 2),
            "speedup_multiple": round(before_ns / after_ns, 2),
        }
    payload = {
        "schema_version": 1,
        "issue": 77,
        "fixture": before["fixture"],
        "methodology": before["methodology"],
        "environment": before["environment"],
        "revisions": {"before": before["source"], "after": after["source"]},
        "benchmarks": benchmarks,
        "comparisons": comparisons,
        "semantic_comparison": {
            "bit_equal": bit_equal,
            "maximum_absolute_error": 0.0 if bit_equal else None,
            "digests": before["semantic_digests"] if bit_equal else {},
            "scope": ["processed RGBA", "next history", "next effect coverage", "FrameDiagnostics"],
        },
        "notes": [
            "Before and after used separate clean worktrees on the same machine and one "
            "committed runner.",
            "Core workloads begin from non-reset history; end-to-end workloads include one "
            "reset frame plus the named eligible frames.",
            "End-to-end measurements use process_sequence with deterministic in-memory pass "
            "reads and marker-only writes, including recovery/report commits but excluding "
            "EXR codec cost.",
            "mixed_perf_1 uses the three-frame canonical 1080p Extreme core shape with "
            "nonempty target coverage.",
            "Benchmarks are developer evidence, not CI timing gates.",
        ],
    }
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(serialized, end="")
    else:
        output.write_text(serialized)


def main() -> None:
    args = _parse_args()
    if args.compare_before is not None:
        _compare(args.compare_before, args.compare_after, args.output)
        return
    beauty, history, motion, empty = _fixtures()
    mixed = empty.copy()
    mixed[HEIGHT // 5 : HEIGHT * 4 // 5, WIDTH // 5 : WIDTH * 4 // 5] = 1.0
    operations: dict[str, Callable[[], object]] = {
        "empty_hard_core": lambda: _run_core(
            1, FeedbackMode.HARD_LOCALIZED, beauty, history, motion, empty
        ),
        "empty_trail_core": lambda: _run_core(
            1, FeedbackMode.TRAIL, beauty, history, motion, empty
        ),
        "preroll_30_core": lambda: _run_core(
            30, FeedbackMode.TRAIL, beauty, history, motion, empty
        ),
        "preroll_60_core": lambda: _run_core(
            60, FeedbackMode.TRAIL, beauty, history, motion, empty
        ),
        "mixed_perf_1_core": lambda: _run_mixed(beauty, history, motion, empty),
        "empty_hard_end_to_end": lambda: _run_end_to_end(
            1, FeedbackMode.HARD_LOCALIZED, beauty, motion, empty
        ),
        "empty_trail_end_to_end": lambda: _run_end_to_end(
            1, FeedbackMode.TRAIL, beauty, motion, empty
        ),
        "preroll_30_end_to_end": lambda: _run_end_to_end(
            30, FeedbackMode.TRAIL, beauty, motion, empty
        ),
        "preroll_60_end_to_end": lambda: _run_end_to_end(
            60, FeedbackMode.TRAIL, beauty, motion, empty
        ),
        "mixed_perf_1_end_to_end": lambda: _run_end_to_end(
            2, FeedbackMode.TRAIL, beauty, motion, mixed
        ),
    }
    benchmarks = {
        name: _measure(operation, args.warmups, args.measured)
        for name, operation in operations.items()
    }
    semantic_digests = {
        "empty_hard": _digest(
            _run_core(1, FeedbackMode.HARD_LOCALIZED, beauty, history, motion, empty)
        ),
        "empty_trail": _digest(_run_core(1, FeedbackMode.TRAIL, beauty, history, motion, empty)),
        "preroll_30": _digest(_run_core(30, FeedbackMode.TRAIL, beauty, history, motion, empty)),
        "preroll_60": _digest(_run_core(60, FeedbackMode.TRAIL, beauty, history, motion, empty)),
        "mixed_perf_1": _digest(_run_mixed(beauty, history, motion, empty)),
    }
    payload = {
        "revision": args.revision,
        "source": {"sha": args.source_sha, "feedback_blob": args.source_blob},
        "runner": {"sha": args.runner_sha, "blob": args.runner_blob},
        "fixture": {
            "shape": [HEIGHT, WIDTH, 4],
            "dtype": "float32",
            "deterministic_seed": SEED,
            "preset": "extreme_full_frame_feedback_settings",
        },
        "methodology": {
            "clock": "perf_counter_ns",
            "warmup_count": args.warmups,
            "measured_count": args.measured,
            "statistics": ["median", "minimum", "maximum"],
            "extrapolation_frames": 147,
        },
        "environment": _environment(),
        "benchmarks": benchmarks,
        "semantic_digests": semantic_digests,
    }
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(serialized, end="")
    else:
        args.output.write_text(serialized)


if __name__ == "__main__":
    main()
