# Object Datamosh

Object Datamosh is a modern Blender extension for building object-localized temporal feedback
workflows. The current MVP targets and has been tested with **Blender 5.0.0**. It provides the
user interface, shared contracts, a pure NumPy localized feedback core with hard and trail modes,
a non-destructive Object Index compositor setup, sequential raw-pass rendering, and processing of existing pass
sequences into scene-linear full-float OpenEXR output.

The installable extension source is `src/object_datamosh` and uses
`blender_manifest.toml`. There is no legacy `bl_info` declaration.

## Install the extension shell

Build an installation ZIP from the repository root:

```bash
mkdir -p dist
"$BLENDER_BIN" --command extension build \
  --source-dir src/object_datamosh \
  --output-dir dist
```

In Blender 5.0, open **Edit → Preferences → Get Extensions**, use the menu's
**Install from Disk** action, and choose the ZIP under `dist/`. Enable **Object Datamosh** if it is
not enabled automatically. The panel is in **3D View → Sidebar → Object Datamosh**.

The sidebar currently provides:

- a target-object picker, **Use Active Object**, **Setup Object Index**, and
  **Restore Object Index Setup** actions;
- a **Create Vector Calibration Scene** action for a separate manual-calibration scene;
- the active view-layer name;
- sequence start/end frames, an optional output-directory override, a conservative raw-output
  overwrite toggle, **Render Raw Passes**, **Render and Process**, reset/recovery policy, and
  **Process Existing Passes**;
- Object Index, External Matte, and experimental Cryptomatte source choices;
- Hard Localized / Trail mode, trail decay, persistence, block size,
  motion-channel/direction/axis/gain/clamp/quantization, diffusion, refresh-probability, and
  deterministic-seed controls; and
- a status field and an explicit warning when the blend file has not been saved.

The target assignment operator has a useful poll: it is available only when an active object
exists. Repeated register/unregister calls and registration cycles are idempotent.

## Output directory contract

For a saved file such as `/projects/shot.blend`, the derived root is
`/projects/ODM_shot_object_datamosh`. An explicit absolute sidebar output directory replaces this
root. A Blender-relative override (`//...`) is only accepted when the blend file gives it a safe
anchor. An unsaved blend file always displays a warning: with no absolute override, Object
Datamosh falls back to Blender's temporary directory at `ODM_object_datamosh_unsaved` and shows:

> Save the blend file to use a project-relative output directory.

With an explicit absolute override, it keeps that safe root and instead shows:

> Blend file is unsaved; using the explicit absolute output directory.

A frame uses four-digit padding by default and resolves to:

```text
<root>/
├── raw/
│   ├── beauty/ODM_beauty_0001.exr
│   ├── vector/ODM_vector_0001.exr
│   └── matte/ODM_matte_0001.exr
└── processed/ODM_processed_0001.exr
```

`SequencePaths` and `FramePaths` in `object_datamosh.core.paths` are the single shared naming
contract for later rendering and processing services.

## Shared processing contracts

Pure contracts live under `object_datamosh.core` and do not import `bpy`:

- **Arrays:** images are NumPy `float32` arrays shaped `(height, width, 4)` in scene-linear RGBA;
  mattes are `float32` arrays shaped `(height, width)`.
- **Feedback state:** `FeedbackState` carries RGBA history, selected-object matte history, and the
  frame number. It validates dtype, channel count, and matching dimensions.
- **Sampling:** `bilinear_sample` samples scalar or channel images in pixel coordinates, returns an
  in-bounds validity mask, and returns zero rather than wrapping for invalid coordinates.
- **Frame processing:** `process_frame` accepts beauty, motion, current matte, optional prior state,
  frame number, settings, and an optional forced-reset flag. It returns the processed float32 RGBA
  image and the next `FeedbackState` without importing Blender APIs or using global RNG state.
- **Feedback settings:** immutable `FeedbackSettings` contains all sidebar feedback controls and
  validates probabilities, block size, and non-negative motion controls.
- **Matte providers:** `ObjectIndexMatteProvider` resolves rendered Object Index mattes;
  `ExternalMatteProvider` safely resolves a numbered external sequence without allowing its
  filename pattern to escape the selected directory. The
  `CryptomatteMatteProvider` contract intentionally fails with a clear `NotImplementedError`:
  decoding remains experimental and is not part of the MVP.
- **Image I/O:** `ImageSequenceIO` is the processing boundary. `BlenderImageIO` is its Blender
  implementation and reads/writes full-float RGBA OpenEXR using temporary `ODM_` Image
  data-blocks. Blender 5.0 compositor multilayer pass files are decoded through the extension's
  narrow NumPy/standard-library scanline ZIP reader because Blender identifies those files but
  does not expose their pixels through `Image.pixels`. Matte files use scalar coverage from the
  EXR red channel; `read_mask` returns that
  channel as a contiguous `(height, width)` `float32` array. The implementation removes temporary
  data-blocks and restores render image settings in `finally` paths.
- **Ownership:** extension-created data uses the `ODM_` prefix and the
  `object_datamosh_owned` custom-property tag. Helpers live in
  `object_datamosh.core.ownership`.

`object_datamosh.ui.feedback_settings_for_scene` copies Blender properties into the pure settings
contract, preventing Blender-facing services from redefining feedback options.

## Object Index compositor setup

Choose a target and click **Setup Object Index**. The setup assigns the lowest available nonzero
Object Index to that object, enables Vector and Object Index passes on the active view layer, and
adds one tagged `ODM_Object_Index_Setup` frame containing deterministic Render Layers, ID Mask,
and beauty/vector/matte File Output nodes. The Render Layers **Object Index** output (called
`IndexOB` in older Blender terminology) feeds the ID Mask, whose index is the assigned target
index. Output nodes write full-float scene-linear OpenEXR files using the shared `SequencePaths`
layout and four-digit frame token by default.

Setup is idempotent for the same target. If another target is already configured, restore first;
this prevents an abandoned pass-index change. **Restore Object Index Setup** removes only tagged
owned nodes and restores the target's prior pass index plus the view layer's prior Vector and
Object Index pass-enable values. Existing compositor node trees and unrelated nodes or links are
left in place. When a scene had no compositor tree, setup creates a tagged `ODM_` tree and cleanup
removes it only if it remains empty. The service does not touch a user output/composite path.

The Blender 5.0.0 smoke render uses Cycles because the tested Eevee configuration did not emit its
Object Index socket. Engine-specific pass availability should be confirmed before production
rendering; setup itself does not change the user's render engine.

## Render raw passes

Choose the target and run **Setup Object Index**, set the sequence range and output directory, then
click **Render Raw Passes**. The action renders one frame at a time through Blender and writes
separate beauty, vector, and Object Index matte sequences under `raw/`. On the tested Blender
5.0.0 configuration, the emitted names are:

```text
raw/beauty/ODM_beauty_0001.exr
raw/vector/ODM_vector_0001.exr
raw/matte/ODM_matte_0001.exr
```

The files are scene-linear, ZIP-compressed, full-float RGBA OpenEXR. Rendering inspects each pass
directory after every frame and returns the paths Blender actually emitted through `FramePaths`;
it does not hand later orchestration an unverified filename assumption. The public
`render_raw_passes` service returns a `RawRenderResult` containing those frames in strict order.
It does not invoke feedback processing.

Existing files in the configured range stop the action before rendering unless **Overwrite Raw
Passes** is explicitly enabled. Rendering temporarily retargets only the extension-owned File
Output nodes. Their prior directories and filename patterns, along with the scene's current frame,
are restored after success, failure, or cancellation. Progress always closes. Cancellation is
observed between frames, so already completed files form a bounded recovery point; the extension
does not delete them. Resolve the cause and rerun with overwrite enabled only when replacing those
raw files is intended.

Object Index remains render-engine dependent. Use Cycles for the documented Blender 5.0.0 path,
or verify that the chosen engine exposes and emits Image, Vector, and Object Index before a
production render. A missing pass fails with the pass name and inspected directory.

## Render and Process

After Object Index setup, **Render and Process** runs the raw renderer to completion and then hands
its exact discovered `FramePaths` to sequential processing. It never reconstructs raw input names
for that handoff. Status changes from **Rendering raw passes...** to **Processing rendered
passes...**, and a failure identifies the active phase.

Processing does not start if rendering fails or is cancelled. The raw renderer accepts only one
newly emitted file per pass and frame, so an unchanged stale file or an incomplete frame fails the
render phase instead of becoming processing input. A render interruption retains only its complete
raw-frame prefix. A processing interruption retains raw inputs and the atomically recorded
processed-frame prefix; use **Process Existing Passes → Resume** with unchanged settings to
continue. The combined action intentionally starts a new reprocess run rather than silently
resuming old processed output. Existing raw or processed files are still protected by their
separate overwrite toggles and are never deleted automatically.

## Process existing pass sequences

**Process Existing Passes** is independent of compositor setup and rendering. It reads the
configured frame range from the output-directory contract above, in ascending frame order. Each
frame requires:

```text
raw/beauty/ODM_beauty_<frame>.exr
raw/vector/ODM_vector_<frame>.exr
raw/matte/ODM_matte_<frame>.exr       # Object Index mode
```

For an external matte, choose **External Matte** and a directory containing
`matte_<frame>.exr` with the same four-digit frame padding. Beauty and vector inputs must be
scene-linear float RGBA OpenEXRs with identical dimensions. Matte coverage is read from the red
channel and must have matching dimensions. Unsupported channels, unreadable files, missing
frames, non-finite values, and dimension mismatches stop processing with the affected pass or
shape identified.

The configured first frame always initializes clean history. **Reset Frames** accepts a
comma-separated expression such as `12, 24, 36`; duplicates and whitespace are ignored, and each
listed frame also initializes from clean beauty. Every other frame receives the state returned by
the preceding frame. A resolution change either stops before history is reused (the conservative
default) or performs a documented clean reset, according to **Resolution Change**.

Each result is written to `processed/ODM_processed_<frame>.exr` as scene-linear, ZIP-compressed,
full-float RGBA OpenEXR. Processing also atomically updates
`processed/ODM_sequence_manifest.json`. This compact JSON manifest records the frame range,
ordered completed-frame prefix, explicit resets, resolution policy, and a SHA-256 fingerprint of
feedback and matte-provider settings. It contains no image data. Output from different settings,
a changed range, or discontinuous completion metadata is rejected rather than silently reused.

**Reprocess** starts from the configured first frame. Existing outputs stop it unless **Overwrite
Processed Frames** is enabled; enabling overwrite is explicit permission to replace the complete
range. If a reprocess is interrupted, old files later in the range remain on disk but stay pending
in the manifest and are never trusted as current output. **Resume** requires a compatible
manifest, reconstructs state only from its last contiguous completed output and selected-object
matte, and continues with pending frames. Existing pending files are replaced. If recorded history
is missing, unreadable, has invalid dimensions, or otherwise violates the state contract,
**Missing History: Stop** fails without processing; **Reset** rolls the recoverable boundary back
and reprocesses from that frame with clean history. Resume never skips a gap.

Progress and manifest updates occur at complete-frame boundaries and progress always closes after
success, failure, or cancellation. A cancellation therefore leaves an exact safe restart point.
Completed files are retained and are never deleted automatically. Use Resume for the same range
and settings, or choose Reprocess with overwrite when deliberately replacing the full sequence.

## Localized feedback semantics

Motion channels contain a forward displacement `(x, y)` from a history pixel to its location in
the current frame. For a current pixel `(x, y)`, the processor therefore samples history at
`(x - displacement_x, y - displacement_y)`. RG maps R to X and G to Y; BA maps B to X and A to Y.
**Reverse Motion** negates both components, while **Flip X** and **Flip Y** negate individual axes.
These overrides are intentionally exposed because pass conventions must be checked with the
manual calibration workflow below.

The processor applies motion gain, direction/axis overrides, and a direction-preserving magnitude
clamp. It computes a current-matte-weighted mean vector for each block, including partial blocks at
odd image edges, then expands that representative vector over the block. A positive quantization
value rounds each component to the nearest multiple of that value; zero disables quantization.
Diffusion adds an independent per-block X/Y offset in `[-Diffusion, +Diffusion]`. Refresh selects
whole blocks to use clean beauty. Both choices are deterministic hashes of seed, frame number, and
block coordinates and do not touch NumPy's global random state.

A missing prior state or **Force Reset** initializes history from clean beauty. Otherwise, warped
history color is sampled premultiplied by its selected-object matte and is accepted only where the
sample coordinate, warped history matte, and contributing history pixels are valid. Unselected
background color therefore cannot enter history at a matte edge.

**Hard Localized** multiplies persistence by current and warped matte coverage; refresh makes that
weight zero. Pixels outside the current matte consequently equal clean beauty exactly. This is the
conservative default and preserves the original outside-mask invariant.

**Trail** advects selected-object history with the same motion field, multiplies the warped history
coverage by **Trail Decay**, and combines that coverage with the current matte for the next frame.
Output can extend beyond the current silhouette only where that advected selected-object coverage
remains nonzero. Coverage never comes from background color. A decay of `0` removes old trail
coverage after one frame; `1` retains reachable coverage without decay. The default `0.85` fades
trails conservatively. Persistence still controls the history-color blend. Invalid or out-of-bounds
warped history is rejected, and premultiplied sampling limits edge contamination.

Explicit reset frames, the first frame, configured resolution-change resets, and missing-history
recovery resets clear both color and trail history. Resume reconstructs trail coverage across the
contiguous completed prefix before continuing, so a resumed trail sequence matches its sequential
state rather than falling back to the last frame's raw matte.

All inputs are finite NumPy `float32` arrays. Beauty and motion are `(height, width, 4)`; matte is
`(height, width)` coverage in `[0, 1]`. Processing is sequential: pass the returned state to the
next frame, or request a reset when history must be discarded.

## Manual vector calibration

Click **Create Vector Calibration Scene** to add a separate, owned `ODM_Vector_Calibration` scene.
The operator does not switch, overwrite, or mutate the current scene; choose the new scene from
Blender's scene selector when ready. It contains a white emissive 2-by-1 rectangle, a transparent
black world, and an orthographic camera. The rectangle moves linearly along world X from
`(-2, 0, 0)` on frame 1 to `(2, 0, 0)` on frame 8. Resolution is 256 by 256, the orthographic
scale is 7, Cycles is selected, motion blur is off, and Vector plus Object Index outputs are
already configured. The setup writes through the normal raw-output path contract.

To calibrate a Blender version, render frames 1-8 with **Render Raw Passes**, then inspect the
rectangle in the Vector pass using the Compositor, Image Editor, or another float-EXR viewer:

1. Compare RG and BA. At an endpoint, the pair that still contains horizontal motion identifies
   the direction toward the existing neighboring frame. For temporal feedback from the preceding
   frame, select the pair that is populated on frame 8; on tested Blender 5.0.0 this is **RG**.
2. The rectangle moves right. If the chosen X value is negative, enable **Reverse Motion** (or
   **Flip X** when only X needs correction). Tested Blender 5.0.0 produced approximately
   `R = -20.898`, `G = 0` on interior rectangle pixels, so the documented starting point is RG,
   Reverse Motion enabled, and Motion Gain 1.0.
3. Check scale against the worked value: `(4 world units / 7 frame intervals) / 7 ortho units ×
   256 pixels = 20.898 pixels/frame`. Adjust **Motion Gain** only if the measured magnitude differs.
4. To check vertical convention, temporarily move both location keyframes from X to Y while
   preserving their values, rerender, and inspect the chosen Y channel. A screen-up movement must
   become positive under the processor's pixel convention; toggle **Flip Y** if it does not.
5. Confirm reversal by rendering the same motion in the opposite direction. The chosen X channel
   must change sign. Restore the deterministic X keyframes or create a fresh calibration scene
   afterward.

Both RG/BA selection, whole-vector reversal, per-axis flips, and gain remain explicit sidebar
overrides because engines and Blender releases may emit different conventions. The measured 5.0.0
result is a starting point, not automatic inference. The last frame has no next-frame BA vector
and the first frame has no previous-frame RG vector; use interior frames for magnitude checks.

The background-compatible creation check is:

```bash
"$BLENDER_BIN" --background --factory-startup --python tests/create_calibration_scene.py
```

Visual channel interpretation and the temporary Y/reversed-motion checks remain interactive.

## Architecture

```text
Blender sidebar / operators
        │
        ├── Object Index setup/restore ───────────────── owned compositor nodes
        ├── Render Raw Passes ────────────────────────── sequential Blender render service
        ├── Render and Process ───────────────────────── exact discovered-path phase handoff
        ├── Process Existing Passes ─────────────────── feedback + EXR + recovery manifest
        ├── Create Vector Calibration Scene ─────────── separate owned Blender scene
        ├── SequencePaths + matte-provider contracts ── render/process boundaries
        │
        ├── FeedbackSettings / FeedbackState ────────── hard/trail NumPy feedback core
        │
        └── ImageSequenceIO ─────────────────────────── BlenderImageIO (bpy)

object_datamosh.core: NumPy + Python only; never imports bpy
```

No background thread calls Blender APIs. Runtime state belongs to Blender scenes, tagged Blender
data, or returned immutable values; there is no mutable module-level runtime state. Setup and
cleanup never delete, disconnect, or replace unrelated compositor nodes and restore pass settings
that they change.

## Development and verification

Install the development environment:

```bash
uv sync
```

Set `BLENDER_BIN` to the tested Blender executable, then run all repository gates:

```bash
uv run ty check
uv run pytest -q
"$BLENDER_BIN" --background --factory-startup --python tests/blender_smoke_test.py
"$BLENDER_BIN" --command extension validate src/object_datamosh
mkdir -p dist
"$BLENDER_BIN" --command extension build \
  --source-dir src/object_datamosh \
  --output-dir dist
```

The development-only Blender 5.0 stubs support static checking outside Blender. NumPy is a
development dependency and is bundled with Blender at runtime; the extension declares no
third-party runtime dependency.

## Current limitations

- Resume is deliberately range-based and sequential. It does not splice arbitrary processed
  fragments, migrate old manifest schemas, or delete stale files; incompatible or discontinuous
  runs require explicit full-range reprocessing.
- Trail mode follows only the available selected-object matte and vector information. Occlusions,
  disocclusions, inaccurate vectors, and low-resolution mattes can shorten or distort trails; it
  does not infer hidden geometry or admit unrelated-object/background history.
- Object Index is the MVP selected-object matte. External mattes follow the documented
  numbered-file contract. Cryptomatte appears as experimental UI/contract surface only; decoding
  is not implemented. Object Index availability remains render-engine dependent.
- The background smoke test verifies registration, the complete emitted sidebar control surface,
  target assignment and status, path derivation, setup idempotency, cleanup/restoration, unrelated
  node survival, separate vector-calibration scene creation and ownership, the raw-render
  operator, collision refusal, bounded cancellation, a two-frame
  Cycles beauty/vector/matte render, emitted filename discovery, a two-frame combined render and
  process run, full-float EXR contracts, temporary
  image cleanup, render-setting restoration, processing fixture-generated two-frame pass
  sequences, processed EXR contracts, hard-localized output, trail controls, and processed-output
  collision refusal. Visual node layout, sidebar polish, interactive cancellation, calibration pass
  interpretation (especially the Y-axis and reversed-motion checks), and control behavior still
  require a manual foreground Blender check.
