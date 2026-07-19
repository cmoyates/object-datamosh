from __future__ import annotations

import runpy
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

from object_datamosh.core.paths import SequencePaths

_SCRIPT = Path(__file__).parents[1] / "scripts" / "issue26_evidence.py"
_NAMESPACE = runpy.run_path(str(_SCRIPT), run_name="issue26_evidence_test")
completed_processed_prefix = _NAMESPACE["completed_processed_prefix"]
completed_raw_prefix = _NAMESPACE["completed_raw_prefix"]
raw_render_intervals = cast(
    Callable[[Sequence[Mapping[str, object]]], list[tuple[float, float]]],
    _NAMESPACE["raw_render_intervals"],
)


def test_completed_raw_prefix_requires_all_passes_and_no_next_frame(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    for number in (1, 2):
        frame = paths.frame(number)
        for path in (frame.beauty, frame.vector, frame.matte):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()

    assert completed_raw_prefix(paths, end=10) == [1, 2]


def test_completed_processed_prefix_matches_manifest(tmp_path: Path) -> None:
    paths = SequencePaths(tmp_path)
    for number in (1, 2):
        frame = paths.frame(number)
        frame.processed.parent.mkdir(parents=True, exist_ok=True)
        frame.processed.touch()
    manifest = paths.root / "processed" / "ODM_sequence_manifest.json"
    manifest.write_text('{"completed_frames": [1, 2]}', encoding="utf-8")

    assert completed_processed_prefix(paths, end=10) == [1, 2]


def test_raw_render_interval_accepts_render_completion() -> None:
    events = [
        {"event": "raw_render_active", "frame": 1, "time": 1.0},
        {
            "event": "render_complete",
            "stage": "raw_escape_cancel",
            "frame": 1,
            "time": 2.0,
        },
    ]

    assert raw_render_intervals(events) == [(1.0, 2.0)]


def test_raw_render_interval_accepts_direct_render_cancellation() -> None:
    events = [
        {"event": "raw_render_active", "frame": 1, "time": 1.0},
        {
            "event": "render_cancel",
            "stage": "raw_escape_cancel",
            "frame": 1,
            "time": 1.5,
        },
    ]

    assert raw_render_intervals(events) == [(1.0, 1.5)]
