# Blender 5.0 modal render investigation

Date: 2026-07-19

Platform: Blender 5.0.0 (`a37564c4df7a`), macOS, Apple Silicon

## Public API inspected

The installed Blender Python API exposes `bpy.ops.render.render` with `animation`, `write_still`,
`use_viewport`, `use_sequencer_scene`, `layer`, `scene`, `frame_start`, and `frame_end` properties.
The application-handler lists relevant to one render are:

- `bpy.app.handlers.render_init`
- `render_pre`
- `render_post`
- `render_write`
- `render_complete`
- `render_cancel`

There is no public `RENDER_OT_cancel` operator in this build. Although dynamic attribute lookup can
produce `bpy.ops.render.cancel`, asking for its RNA type raises `KeyError`. The available
`bpy.ops.render.view_cancel` operator only closes the render view; its installed description is
“Cancel show render view,” so it is not a safe programmatic render-cancellation API.

The independent handler probe in this Blender build invoked callbacks with the rendered scene. API
contexts may also provide a dependency-graph argument, so Object Datamosh accepts that second
argument optionally without depending on it. Each adapter callback captures the expected scene and
scene-owned run identity. It ignores a callback unless both still match the active run.

## Invocation probes

### Standalone foreground `INVOKE_DEFAULT`

A foreground timer invoked:

```python
bpy.ops.render.render("INVOKE_DEFAULT", scene=scene.name, write_still=False)
```

The call returned `{'RUNNING_MODAL'}` immediately. A successful Workbench probe observed this
ordering (seconds from probe start):

```text
render_init      0.297
render_pre       0.297
operator return  0.297  {'RUNNING_MODAL'}
render_post      0.304
render_complete  0.304
```

`render_write` did not fire because this probe did not request a still write. `render_complete` is
the successful terminal event; `render_post` alone is not treated as completion.

### Nested foreground `INVOKE_DEFAULT`

The same asynchronous invocation was then launched from the Object Datamosh modal operator. It
rendered and emitted all three frame files, but Blender called the owning operator's cancellation
path when the nested render operator completed. The Object Datamosh run therefore ended as
cancelled after its first verified frame. Nested asynchronous operator ownership is not reliable in
Blender 5.0.0 and is not used by the extension.

### Modal frame-boundary fallback

Object Datamosh uses this reliable fallback from each owned timer boundary:

```python
bpy.ops.render.render(
    "EXEC_DEFAULT",
    scene=expected_scene.name,
    layer=expected_view_layer.name,
)
```

Temporary `render_complete` and `render_cancel` handlers are installed immediately before that
call and removed after its terminal result is consumed. A foreground two-frame Cycles probe
completed both frames and the parent modal operation cleanly. Its observed boundaries were:

```text
operator invoke -> RUNNING_MODAL
frame 1 render_init/render_pre
frame 1 render_post/render_complete
runtime boundary -> current frame 2, completed work 1
frame 2 render_init/render_pre
frame 2 render_post/render_complete
terminal runtime -> COMPLETED, completed work 2, progress 1.0
```

The exact discovered beauty, Vector, and matte paths were retained after each completion. Owned
File Output paths are active only for that atomic render/discovery timer step and are restored before
yielding; finalization restores the original scene frame and any partially active output context.

This fallback yields to Blender between frames, not during an individual frame. A 256×256 Cycles
foreground probe scheduled a 50 ms application heartbeat and recorded **zero heartbeats while a
frame render was active**. Therefore an individual raw frame can temporarily block the UI. The
extension does not claim fully responsive raw rendering.

The scene-visible progress changed after frame 1 and before frame 2, and the modal lifecycle issued
its safe 3D View sidebar redraw request at that verified boundary. The pure lifecycle test verifies
that a 3D View area receives `tag_redraw()` after progress publication. The background Blender smoke
test verifies the runtime boundary but intentionally has no foreground windows.

A separate foreground Blender probe opened the sidebar's active category, registered an
instrumented panel, published two successive frame states, and called `tag_redraw()` at each
boundary. Blender invoked the panel draw once for frame 1 and once again for frame 2
(`frame1_draws=1`, `frame2_total_draws=2`, observed stages `[2, 3]`). This verifies that the sidebar
repainted at each foreground frame boundary; it does not claim that the blocking fallback can
repaint during an individual render.

## Completion and cancellation behavior

- At most one frame render is launched at a time.
- Advancement occurs only after `render_complete` (or synchronous `FINISHED`) and successful
  output discovery.
- `render_cancel` is terminal for the active frame and that unverified frame is not added to the
  completed prefix.
- Escape handled by the parent modal operator and the sidebar **Cancel** button publish
  `Cancel requested; waiting for a safe boundary...` immediately when Blender's event loop is
  available. No later frame is launched.
- During the `EXEC_DEFAULT` fallback, the extension cannot receive its own modal events until the
  current call returns. Object Datamosh does not invoke an undocumented cancellation API.
- A foreground active-render Escape probe used the public asynchronous invocation solely to observe
  Blender's own key handling. Escape produced this terminal ordering (seconds from probe start):
  `invoke 1.000`, `render_init 1.026`, operator return `RUNNING_MODAL` 1.026, `render_pre 1.026`,
  `render_post 12.151`, `render_cancel 12.151`. No `render_complete` event fired. Cancellation was
  not immediate, but Blender eventually reached the documented cancellation boundary.
- If the current render cannot be interrupted, cancellation takes effect after that frame is
  complete and its three files are verified. Completed files are preserved.
- The live foreground Escape probe used macOS System Events to send a real key event because Blender
  exposes no supported Python event-injection API. Escape-before-launch, parent-modal Escape,
  Cancel-button, completion-after-pending-cancel, and adapter `render_cancel` boundaries are also
  covered through deterministic controller/Blender tests.

## Background mode

In `--background` mode, even an `INVOKE_DEFAULT` call returned `{'FINISHED'}` synchronously, and a
script that owns Blender's main thread cannot pump real modal window events. The registered
**Render Raw Passes** operator therefore drives the incremental session synchronously in background
mode and returns `FINISHED`; a Blender smoke scenario invokes that registered operator directly and
verifies its files and unlocked runtime. A separate deterministic WindowManager recorder exercises
the foreground modal startup, timer ownership, progress boundaries, cancellation, and cleanup while
running under the background smoke harness.

## macOS limitations

- The reliable fallback can show the macOS wait cursor and make Blender appear unresponsive for the
  duration of a long individual frame.
- Focus, Escape delivery, and render-window behavior depend on which Blender window is frontmost;
  no supported Python API can synthesize that foreground key event for a deterministic test.
- There is no verified safe public API for Object Datamosh to force-cancel the active render.
- Blender application handlers are process-wide lists. The adapter therefore scopes its exact
  callback objects to one active frame, validates scene plus run identity, and removes only those
  objects during idempotent cleanup.
