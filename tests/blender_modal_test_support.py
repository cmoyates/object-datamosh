"""Reusable deterministic boundaries for Blender modal-operation smoke tests."""

from __future__ import annotations

from typing import Any


class ModalWindowManagerRecorder:
    """Record timer/progress ownership without depending on Blender's live event loop."""

    shared_timer = object()

    def __init__(self, *, fail_progress_update_at: int | None = None) -> None:
        self.events: list[tuple[str, object]] = []
        self.timer = self.shared_timer
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

    def report(self, level: set[str], message: str) -> None:
        self.reports.append((level, message))


class LayoutRecorder:
    """Minimal Blender layout double with shared observations across nested boxes."""

    def __init__(self, parent: LayoutRecorder | None = None) -> None:
        if parent is None:
            self.properties: set[str] = set()
            self.operators: set[str] = set()
            self.labels: list[str] = []
            self.boxes: list[LayoutRecorder] = []
        else:
            self.properties = parent.properties
            self.operators = parent.operators
            self.labels = parent.labels
            self.boxes = parent.boxes
        self.alert = False
        self.enabled = True

    def box(self) -> LayoutRecorder:
        child = LayoutRecorder(self)
        self.boxes.append(child)
        return child

    def row(self, *, align: bool = False) -> LayoutRecorder:
        del align
        return self

    def prop(self, data: object, property_name: str) -> None:
        del data
        self.properties.add(property_name)

    def operator(self, operator_name: str) -> None:
        self.operators.add(operator_name)

    def label(self, *, text: str, icon: str | None = None) -> None:
        del icon
        self.labels.append(text)
