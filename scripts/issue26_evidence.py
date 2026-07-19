from __future__ import annotations

from collections.abc import Mapping, Sequence


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
