from __future__ import annotations

import runpy
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

_SCRIPT = Path(__file__).parents[1] / "scripts" / "issue26_evidence.py"
_NAMESPACE = runpy.run_path(str(_SCRIPT), run_name="issue26_evidence_test")
raw_render_intervals = cast(
    Callable[[Sequence[Mapping[str, object]]], list[tuple[float, float]]],
    _NAMESPACE["raw_render_intervals"],
)


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
