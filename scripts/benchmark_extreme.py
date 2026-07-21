"""Reproducible 1080p Extreme-path benchmark; run with Blender's Python."""

from __future__ import annotations

import argparse
import json
import platform
import resource
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from object_datamosh.benchmarking import (  # noqa: E402
    summarize_processing_reports,
    summarize_samples,
)
from object_datamosh.blender_image_io import BlenderImageIO  # noqa: E402
from object_datamosh.core.block_preparation import prepare_blocks  # noqa: E402
from object_datamosh.core.contracts import FeedbackState  # noqa: E402
from object_datamosh.core.exr import (  # noqa: E402
    _undo_zip_preprocessing,
    read_full_float_rgba,
)
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


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


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


def _write_multilayer_read_fixtures(
    root: Path,
    beauty: np.ndarray,
    motion: np.ndarray,
    matte: np.ndarray,
) -> dict[str, Path]:
    """Render production-shaped compositor multilayer EXRs for read-route measurements."""
    scene = bpy.data.scenes.new("ODM_Benchmark_EXR_Reads")
    camera_data = bpy.data.cameras.new("ODM_Benchmark_EXR_Reads_Camera")
    camera = bpy.data.objects.new("ODM_Benchmark_EXR_Reads_Camera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    tree = bpy.data.node_groups.new("ODM_Benchmark_EXR_Reads_Tree", "CompositorNodeTree")
    scene.compositing_node_group = tree
    matte_rgba = np.repeat(matte[..., None], 4, axis=2).astype(np.float32, copy=False)
    fixtures = {"beauty": beauty, "vector": motion, "matte": matte_rgba}
    images: list[Any] = []
    try:
        cast(Any, scene.render).engine = "BLENDER_WORKBENCH"
        scene.render.resolution_x = WIDTH
        scene.render.resolution_y = HEIGHT
        scene.render.resolution_percentage = 100
        for pass_name, pixels in fixtures.items():
            image = bpy.data.images.new(
                f"ODM_Benchmark_{pass_name}",
                width=WIDTH,
                height=HEIGHT,
                alpha=True,
                float_buffer=True,
            )
            images.append(image)
            cast(Any, image.colorspace_settings).name = "Linear Rec.709"
            cast(Any, image.pixels).foreach_set(np.ascontiguousarray(pixels[::-1]).ravel())
            image_node = cast(Any, tree.nodes.new("CompositorNodeImage"))
            image_node.image = image
            output = cast(Any, tree.nodes.new("CompositorNodeOutputFile"))
            output.directory = str(root)
            output.file_name = f"ODM_{pass_name}_####"
            output.format.file_format = "OPEN_EXR_MULTILAYER"
            output.format.color_mode = "RGBA"
            output.format.color_depth = "32"
            output.format.exr_codec = "ZIP"
            output.save_as_render = False
            output.file_output_items.clear()
            item = output.file_output_items.new("RGBA", "Image")
            item.override_node_format = False
            item.save_as_render = False
            tree.links.new(image_node.outputs["Image"], output.inputs["Image"])
        scene.frame_set(1)
        bpy.ops.render.render(scene=scene.name)
    finally:
        scene.compositing_node_group = None
        bpy.data.scenes.remove(scene)
        bpy.data.node_groups.remove(tree)
        for image in images:
            bpy.data.images.remove(image)
        bpy.data.objects.remove(camera)
        bpy.data.cameras.remove(camera_data)
    paths = {name: root / f"ODM_{name}_0001.exr" for name in fixtures}
    if missing := [str(path) for path in paths.values() if not path.is_file()]:
        raise RuntimeError(f"Compositor benchmark fixtures were not written: {missing}")
    return paths


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
        read_fixtures = _write_multilayer_read_fixtures(Path(temporary), beauty, motion, matte)

        def custom_matte() -> np.ndarray:
            return np.ascontiguousarray(image_io.read_rgba(read_fixtures["matte"])[..., 0])

        def blender_probe_then_bundled(path: Path) -> np.ndarray:
            image = bpy.data.images.load(str(path), check_existing=False)
            try:
                image.reload()
                if image.channels != 0 or image.type != "MULTILAYER":
                    raise AssertionError(f"expected a compositor multilayer EXR: {path}")
                return read_full_float_rgba(path)
            finally:
                bpy.data.images.remove(image)

        def blender_matte() -> np.ndarray:
            return np.ascontiguousarray(blender_probe_then_bundled(read_fixtures["matte"])[..., 0])

        images_before_reads = len(bpy.data.images)
        custom_reader_samples = {
            "beauty": _measure(
                lambda: image_io.read_rgba(read_fixtures["beauty"]),
                args.warmups,
                args.measured,
            ),
            "vector": _measure(
                lambda: image_io.read_rgba(read_fixtures["vector"]),
                args.warmups,
                args.measured,
            ),
            "matte": _measure(custom_matte, args.warmups, args.measured),
            "all_three": _measure(
                lambda: (
                    image_io.read_rgba(read_fixtures["beauty"]),
                    image_io.read_rgba(read_fixtures["vector"]),
                    custom_matte(),
                ),
                args.warmups,
                args.measured,
            ),
        }
        regular_blender_samples = {
            "beauty": _measure(
                lambda: image_io._read_with_blender(frame.beauty), args.warmups, args.measured
            ),
            "vector": _measure(
                lambda: image_io._read_with_blender(frame.vector), args.warmups, args.measured
            ),
            "matte": _measure(
                lambda: np.ascontiguousarray(image_io._read_with_blender(frame.matte)[..., 0]),
                args.warmups,
                args.measured,
            ),
        }
        blender_probe_samples = {
            "beauty": _measure(
                lambda: blender_probe_then_bundled(read_fixtures["beauty"]),
                args.warmups,
                args.measured,
            ),
            "vector": _measure(
                lambda: blender_probe_then_bundled(read_fixtures["vector"]),
                args.warmups,
                args.measured,
            ),
            "matte": _measure(blender_matte, args.warmups, args.measured),
            "all_three": _measure(
                lambda: (
                    blender_probe_then_bundled(read_fixtures["beauty"]),
                    blender_probe_then_bundled(read_fixtures["vector"]),
                    blender_matte(),
                ),
                args.warmups,
                args.measured,
            ),
        }
        assert len(bpy.data.images) == images_before_reads

        def decode_matte() -> np.ndarray:
            return np.ascontiguousarray(read_full_float_rgba(read_fixtures["matte"])[..., 0])

        bundled_decode_samples = {
            "beauty": _measure(
                lambda: read_full_float_rgba(read_fixtures["beauty"]),
                args.warmups,
                args.measured,
            ),
            "vector": _measure(
                lambda: read_full_float_rgba(read_fixtures["vector"]),
                args.warmups,
                args.measured,
            ),
            "matte": _measure(decode_matte, args.warmups, args.measured),
            "all_three": _measure(
                lambda: (
                    read_full_float_rgba(read_fixtures["beauty"]),
                    read_full_float_rgba(read_fixtures["vector"]),
                    decode_matte(),
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
        # Each overwrite run replaces the report, so collect additional measured reports explicitly.
        processing_reports: list[dict[str, Any]] = []
        for _ in range(args.measured):
            complete_sequence()
            processing_reports.append(
                json.loads(processing_report_path(paths).read_text(encoding="utf-8"))["performance"]
            )

    decoded_rgba_bytes = WIDTH * HEIGHT * 4 * np.dtype(np.float32).itemsize
    bundled_decode_bytes = {
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
            "custom_reader_first": {
                name: summarize_samples(samples) for name, samples in custom_reader_samples.items()
            },
            "blender_probe_first": {
                name: summarize_samples(samples) for name, samples in blender_probe_samples.items()
            },
            "regular_blender_image": {
                name: summarize_samples(samples)
                for name, samples in regular_blender_samples.items()
            },
            "blender_data_block_overhead_ns": {
                name: (
                    summarize_samples(blender_probe_samples[name])["median_ns"]
                    - summarize_samples(custom_reader_samples[name])["median_ns"]
                )
                for name in custom_reader_samples
            },
            "temporary_data_block_count": {
                "custom_reader_first": 0,
                "blender_probe_first": 1,
                "regular_blender_image": 1,
            },
        },
        "bundled_exr_decodes": {
            name: _summarize_throughput(samples, bundled_decode_bytes[name])
            for name, samples in bundled_decode_samples.items()
        },
        "processed_exr_write": summarize_samples(write_samples),
        "complete_sequential_processing": summarize_samples(
            end_to_end_samples, frames_per_sample=SEQUENCE_FRAMES
        ),
        "release_stage_timings": summarize_processing_reports(processing_reports),
    }
    comparable = {
        "block_preparation": benchmarks["block_preparation"]["median_ns"],
        "refresh_diagnostics": benchmarks["refresh_diagnostics"]["median_ns"],
        "pure_core_non_reset_frame": benchmarks["pure_core_non_reset_frame"]["median_ns"],
        "beauty_read": benchmarks["exr_reads"]["custom_reader_first"]["beauty"]["median_ns"],
        "vector_read": benchmarks["exr_reads"]["custom_reader_first"]["vector"]["median_ns"],
        "matte_read": benchmarks["exr_reads"]["custom_reader_first"]["matte"]["median_ns"],
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
        "latest_processing_report_performance": processing_reports[-1],
        "memory": {
            "representative_input_and_state_bytes": sum(
                array.nbytes for array in (beauty, motion, matte, history, state.history_matte)
            ),
            "process_peak_rss_bytes": _peak_rss_bytes(),
            "measurement_scope": "benchmark process peak RSS after all measured workloads",
        },
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
