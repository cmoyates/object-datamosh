"""Reusable deterministic boundaries for Blender modal-operation smoke tests."""

from __future__ import annotations

from typing import Any


class ModalWindowManagerRecorder:
    """Record timer/progress ownership without depending on Blender's live event loop."""

    def __init__(self, *, fail_progress_update_at: int | None = None) -> None:
        self.events: list[tuple[str, object]] = []
        self.timer = object()
        self.windows: tuple[object, ...] = ()
        self._fail_progress_update_at = fail_progress_update_at
        self._progress_update_count = 0

    def progress_begin(self, minimum: int, maximum: int) -> None:
        self.events.append(("progress_begin", (minimum, maximum)))

    def progress_update(self, value: int) -> None:
        self._progress_update_count += 1
        if self._progress_update_count == self._fail_progress_update_at:
            raise RuntimeError("progress publication failed")
        self.events.append(("progress_update", value))

    def progress_end(self) -> None:
        self.events.append(("progress_end", None))

    def event_timer_add(self, interval: float, *, window: object) -> object:
        self.events.append(("timer_add", (interval, window)))
        return self.timer

    def event_timer_remove(self, timer: object) -> None:
        self.events.append(("timer_remove", timer))

    def modal_handler_add(self, operator: object) -> None:
        self.events.append(("modal_handler_add", operator))


class ProcessOperatorHarness:
    """Drive an operator implementation against deterministic Blender boundaries."""

    def __init__(self, operator_type: Any) -> None:
        self._operator_type = operator_type
        self.reports: list[tuple[set[str], str]] = []

    def execute(self, context: Any) -> set[Any]:
        return self._operator_type.execute(self, context)

    def modal(self, context: Any, event: Any) -> set[Any]:
        return self._operator_type.modal(self, context, event)

    def cancel(self, context: Any) -> None:
        self._operator_type.cancel(self, context)

    def _cleanup_session(self) -> None:
        self._operator_type._cleanup_session(self)

    def _finalize(self, phase: Any, status: str) -> None:
        self._operator_type._finalize(self, phase, status)

    def report(self, level: set[str], message: str) -> None:
        self.reports.append((level, message))
