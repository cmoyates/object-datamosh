from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from object_datamosh.core.paths import SequencePaths


def completed_raw_prefix(paths: SequencePaths, *, end: int) -> list[int]:
    """Validate and return the contiguous, complete raw-frame prefix."""
    completed: list[int] = []
    for number in range(1, end + 1):
        frame = paths.frame(number)
        pass_paths = (frame.beauty, frame.vector, frame.matte)
        if number == len(completed) + 1 and all(path.is_file() for path in pass_paths):
            completed.append(number)
        elif any(path.exists() for path in pass_paths):
            raise AssertionError(f"Raw output exists after the complete prefix at frame {number}")
    if not completed:
        raise AssertionError("Raw outputs have no complete frame prefix")
    return completed


def completed_processed_prefix(paths: SequencePaths, *, end: int) -> list[int]:
    """Validate and return a cancelled processing run's manifest-backed prefix."""
    completed = [number for number in range(1, end + 1) if paths.frame(number).processed.is_file()]
    if (
        not completed
        or completed != list(range(1, len(completed) + 1))
        or len(completed) >= end
    ):
        raise AssertionError(
            f"Processed outputs are not a cancelled contiguous prefix: {completed}"
        )
    assert not paths.frame(len(completed) + 1).processed.exists()
    manifest = paths.root / "processed" / "ODM_sequence_manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["completed_frames"] == completed
    return completed


def raw_render_intervals(events: Sequence[Mapping[str, object]]) -> list[tuple[float, float]]:
    """Return active raw-render intervals closed by either Blender terminal callback."""
    intervals: list[tuple[float, float]] = []
    for started in events:
        if started.get("event") != "raw_render_active":
            continue
        start_time = started.get("time")
        frame = started.get("frame")
        if not isinstance(start_time, int | float) or not isinstance(frame, int):
            raise ValueError("Raw-render start event has invalid time or frame")
        end_time: int | float | None = None
        for event in events:
            candidate_time = event.get("time")
            if (
                event.get("event") in {"render_complete", "render_cancel"}
                and event.get("stage") == "raw_escape_cancel"
                and event.get("frame") == frame
                and isinstance(candidate_time, int | float)
                and candidate_time >= start_time
            ):
                end_time = candidate_time
                break
        if end_time is None:
            raise ValueError(f"Raw render for frame {frame} has no terminal event")
        intervals.append((float(start_time), float(end_time)))
    return intervals
