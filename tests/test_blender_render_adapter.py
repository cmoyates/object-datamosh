from __future__ import annotations

from types import SimpleNamespace

from object_datamosh.blender_render_adapter import BlenderRenderAdapter
from object_datamosh.raw_render_operation import RenderEvent, RenderFrameRequest


def test_adapter_ignores_stale_scene_and_run_events() -> None:
    runtime = SimpleNamespace(run_identity="current-run")
    handlers = SimpleNamespace(render_complete=[], render_cancel=[])
    launches: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def render_operator(*args: object, **kwargs: object) -> set[str]:
        launches.append((args, kwargs))
        return {"RUNNING_MODAL"}

    adapter = BlenderRenderAdapter(
        runtime,
        handlers=handlers,
        render_operator=render_operator,
    )
    scene = SimpleNamespace(name="Shot")
    layer = SimpleNamespace(name="Main")
    request = RenderFrameRequest(frame=7, scene=scene, view_layer=layer)

    adapter.launch(request, "current-run")

    assert launches == [(("EXEC_DEFAULT",), {"scene": "Shot", "layer": "Main"})]
    assert adapter.poll() is RenderEvent.ACTIVE
    handlers.render_complete[0](SimpleNamespace(name="Other"), None)
    assert adapter.poll() is RenderEvent.ACTIVE
    runtime.run_identity = "new-run"
    handlers.render_complete[0](scene, None)
    assert adapter.poll() is RenderEvent.ACTIVE
    runtime.run_identity = "current-run"
    handlers.render_complete[0](scene, None)
    assert adapter.poll() is RenderEvent.COMPLETED

    adapter.remove()
    assert handlers.render_complete == []
    assert handlers.render_cancel == []


def test_synchronous_terminal_result_removes_handlers_before_launch_returns() -> None:
    runtime = SimpleNamespace(run_identity="current-run")
    handlers = SimpleNamespace(render_complete=[], render_cancel=[])

    adapter = BlenderRenderAdapter(
        runtime,
        handlers=handlers,
        render_operator=lambda *_args, **_kwargs: {"FINISHED"},
    )
    adapter.launch(
        RenderFrameRequest(
            frame=1,
            scene=SimpleNamespace(name="Shot"),
            view_layer=SimpleNamespace(name="Main"),
        ),
        "current-run",
    )

    assert adapter.poll() is RenderEvent.COMPLETED
    assert handlers.render_complete == []
    assert handlers.render_cancel == []
