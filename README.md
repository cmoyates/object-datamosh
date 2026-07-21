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
- Hard Localized / Trail mode, Target Only / Full Frame history source, invalid-history fallback,
  trail decay, Trail Motion Follow, persistence, block size,
  motion-channel/direction/axis/gain/clamp/quantization, diffusion,
  refresh-probability, and deterministic-seed controls; and
- a status field and an explicit warning when the blend file has not been saved.

The target assignment operator has a useful poll: it is available only when an active object
exists and no Object Datamosh run is active. The sidebar includes scene-owned operation state for
phase, frame range, current frame, completed and total work, progress, status, and cancellation.
Repeated register/unregister calls and registration cycles are idempotent.

## Quick start: use Object Datamosh in Blender

This is the shortest end-to-end workflow for rendering a scene and creating a processed Object
Datamosh sequence. Object Datamosh writes image sequences; it does not replace the scene's render
output or automatically connect the processed sequence to the compositor.

### 1. Prepare the scene

1. Save the `.blend` file. This gives the extension a stable project-relative output location.
2. Open the **Render Properties** and select **Cycles**. Cycles is the tested Blender 5.0 path for
   Image, Vector, and Object Index passes.
3. Set the scene's resolution, camera, lighting, and animation as usual.
4. In the timeline, identify the inclusive frame range you want to process.
5. Select the object that should receive the localized feedback effect.

Object Datamosh preserves the rest of the scene's compositor graph and restores the pass settings
it changes. It does not hide objects, replace materials, or change the scene's render engine.

### 2. Open the extension and choose the target

1. In the **3D View**, press **N** to open the Sidebar.
2. Select the **Object Datamosh** tab.
3. In **Target**, click **Use Active Object**. Alternatively, choose an object directly with the
   **Target Object** picker.
4. Confirm that the displayed view layer is the one you intend to render.
5. In **Matte**, leave **Object Index** selected and click **Setup Object Index**.

Setup assigns the target a free nonzero Object Index and creates tagged `ODM_` compositor output
nodes. It is safe to click again for the same target. To switch targets or view layers, click
**Restore Object Index Setup** first and then perform setup again.

### 3. Choose the range and output location

In **Sequence**:

1. Set **Start** and **End** to the inclusive animation range.
2. Leave **Output Directory** empty to use the derived folder beside the saved `.blend` file, or
   choose an explicit absolute directory.
3. Leave both overwrite controls disabled for the first run. They are deliberate safeguards
   against replacing existing EXRs.
4. Leave **Run Mode** set to **Reprocess** for a new sequence.

The resolved output root is displayed in the panel. For `shot.blend`, the default root is
`ODM_shot_object_datamosh` beside the blend file. Ensure the destination has enough free space for
three full-float raw EXR sequences plus one processed EXR sequence.

### 4. Configure the effect

A conservative first setup is:

| Control | Starting value | Effect |
|---|---:|---|
| **Mode** | Hard Localized | Keeps the effect strictly inside the current target silhouette. |
| **Persistence** | `0.85` | Higher values retain more prior-frame color. |
| **Block Size** | `16` | Larger values produce broader block motion; smaller values retain detail. |
| **Motion Channels** | RG | Tested Blender 5.0 Vector-pass channel pair. |
| **Reverse Motion** | Enabled | Tested Blender 5.0 direction correction. |
| **Motion Gain** | `1.0` | Scales the sampled displacement. |
| **Motion Clamp** | `64` | Limits extreme displacement magnitude. |
| **Motion Quantization** | `1` | Rounds motion into pixel-sized steps; `0` disables it. |
| **Diffusion** | `0` | Increase for deterministic per-block motion jitter. |
| **Refresh Probability** | `0` | Increase to restore random-looking blocks from clean beauty. |
| **Seed** | Any fixed integer | Reproduces diffusion and refresh choices exactly. |

For feedback that persists behind the moving object, choose **Trail** and begin with **Trail
Decay** at `0.85`. Lower decay fades the trail sooner; `0` removes old trail coverage after one
frame, while `1` retains reachable coverage without decay. Under **Full Frame + Trail**, **Trail
Motion Follow** controls mask propagation: `0` keeps prior effect coverage at its old screen
position, `1` follows current object motion (the compatibility default), and intermediate values
blend the two bounded coverages. Target Only semantics do not use this control.

Under **Full Frame**, **Invalid History: Current Beauty** preserves compatibility when a motion
warp leaves the image or encounters invalid history. **Same Screen Position** instead tries the
same pixel in the complete previous processed frame before using current beauty. This control is
irrelevant under **Target Only**, whose history rules are unchanged.

Vector conventions can differ by Blender release, engine, and scene. If the result moves in the
wrong direction or at the wrong scale, use **Create Vector Calibration Scene** and follow
[Manual vector calibration](#manual-vector-calibration) before a long render.

### 5. Render and process

Click **Render and Process**. The extension will:

1. render every frame's beauty, Vector, and Object Index matte passes;
2. verify the actual files emitted for each frame;
3. process those frames sequentially through temporal feedback; and
4. write `processed/ODM_processed_<frame>.exr` plus a recovery manifest.

Watch the panel's phase-specific and overall progress plus Blender's progress display. **Render and
Process** uses one modal workflow, yields between raw renders and processed frames, and responds to
**Escape** or **Cancel** at its next safe boundary in either phase. An individual Blender frame
render can still temporarily block the UI; see
[the Blender 5 modal render investigation](docs/blender-5-modal-render-investigation.md). Completed
raw and processed files are retained rather than deleted.

The two-step path also lets you inspect the raw EXRs before processing. Use it when adjusting
feedback settings without rerendering the scene: keep the raw sequences, choose **Reprocess**,
enable **Overwrite Processed Frames**, and process again.

### 6. Inspect and use the result

Processed frames are written under:

```text
<output root>/processed/ODM_processed_0001.exr
```

Open a frame or the numbered sequence in Blender's Image Editor to inspect it. To use it in a
final Blender composite, add it as an image sequence in the Compositor and connect it where the
processed beauty result belongs in your pipeline. Preserve a scene-linear workflow when applying
view transforms or encoding a delivery format; the extension output is scene-linear full-float
RGBA OpenEXR, not a display-referred movie.

The extension does not delete raw or processed output. After confirming the result, archive or
remove unwanted files yourself as a separate explicit action.

### 7. Resume or restart an interrupted run

- To continue the same processing range and settings, choose **Resume** and click **Process
  Existing Passes**. The manifest identifies the last safe contiguous frame.
- If the recorded history file is unavailable, **Missing History: Stop** preserves the failure for
  diagnosis; **Reset** restarts from the recoverable boundary with clean history.
- To intentionally regenerate the complete processed range, choose **Reprocess**, enable
  **Overwrite Processed Frames**, and process again.
- To intentionally rerender raw inputs, enable **Overwrite Raw Passes** before rendering. This is
  independent of processed-output overwrite permission.

Changing the frame range, matte source, or feedback settings makes an old resume manifest
incompatible by design. Use a full reprocess in that case. Schema-v2 manifests are read only far
enough to report that they cannot prove the complete effective configuration; they are never
migrated by guessing omitted settings. Reprocess while reusing the retained raw beauty, Vector,
and matte passes.

### 8. Restore the temporary Object Index setup

When the raw render is complete, click **Restore Object Index Setup** if you no longer need the
extension's compositor outputs. This removes only tagged Object Datamosh nodes and restores the
object's previous pass index and the view layer's previous pass-enable values. Rendered files are
left untouched.

### Process pass sequences rendered elsewhere

To process compatible existing files without modifying the compositor:

1. Arrange beauty and vector EXRs under the documented `raw/beauty` and `raw/vector` paths.
2. For **Object Index**, also provide the expected `raw/matte` sequence. For **External Matte**,
   choose a directory containing `matte_0001.exr`, `matte_0002.exr`, and so on.
3. Set the matching frame range and output root in the panel.
4. Configure feedback, select **Reprocess**, and click **Process Existing Passes**.

**Setup Object Index is not required** for this processing-only workflow. Cryptomatte is visible as
an experimental choice but is not implemented in the MVP.

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
  mattes are `float32` arrays shaped `(height, width)`. The canonical origin is the displayed
  top-left: row zero is the top scanline, column zero is the leftmost pixel, array Y increases
  downward, and array X increases rightward. Beauty, Vector, matte, history, and processed arrays
  all use this same coordinate system.
- **Feedback state:** `FeedbackState` carries RGBA history, selected-object matte history, and the
  frame number. It validates dtype, channel count, and matching dimensions.
- **Sampling:** `bilinear_sample` samples scalar or channel images in pixel coordinates, returns an
  in-bounds validity mask, and returns zero rather than wrapping for invalid coordinates.
- **Block preparation:** `prepare_blocks` decodes motion and returns reusable compact
  `PreparedBlocks` displacement and refresh grids. Grids include partial right and bottom blocks;
  deterministic diffusion and refresh depend only on settings, frame, and block coordinates.
- **Frame processing:** `process_frame` accepts beauty, motion, current matte, optional prior state,
  frame number, settings, and an optional forced-reset flag. It returns the processed float32 RGBA
  image and the next `FeedbackState` without importing Blender APIs or using global RNG state.
- **Feedback settings:** immutable `FeedbackSettings` contains all feedback controls, including
  the `HistorySource` choice (`TARGET_ONLY` by default or `FULL_FRAME`), and validates
  probabilities, block size, non-negative motion controls, and supported mode/source combinations.
- **Matte providers:** `ObjectIndexMatteProvider` resolves rendered Object Index mattes;
  `ExternalMatteProvider` safely resolves a numbered external sequence without allowing its
  filename pattern to escape the selected directory. The
  `CryptomatteMatteProvider` contract intentionally fails with a clear `NotImplementedError`:
  decoding remains experimental and is not part of the MVP.
- **Image I/O:** `ImageSequenceIO` is the processing boundary. `BlenderImageIO` reads supported
  scanline full-float RGBA ZIP/ZIPS OpenEXRs with the bundled NumPy/standard-library decoder before
  creating any Blender data-block. Explicitly unsupported valid variants use a temporary tagged
  `ODM_` Image fallback; malformed/truncated input and ordinary I/O errors retain their original
  decoder or filesystem error instead of falling back. Writes continue to use a temporary owned
  Image. Blender's `Image.pixels` buffer starts at the displayed bottom-left, so
  `BlenderImageIO` performs one vertical row conversion when entering or leaving that buffer.
  OpenEXR scanline Y starts at the displayed top, so the narrow NumPy/standard-library scanline ZIP
  reader maps compositor multilayer scanlines directly to canonical rows. No orientation transform
  occurs in the feedback core, and the Vector Y component is unchanged because beauty, Vector, and
  matte receive the same pass-boundary row mapping. Matte files use scalar coverage from the EXR
  red channel; `read_mask` returns that channel as a contiguous `(height, width)` `float32` array.
  Modal processing binds image writes to its initiating scene rather than the mutable active
  context. The implementation removes temporary data-blocks and restores render image settings in
  `finally` paths. These boundaries were exercised in Blender 5.2.0 LTS with an asymmetric 5×3
  fixture through `foreach_get`, `foreach_set`, `Image.save_render`, compositor File Output,
  regular-EXR reopen, and the custom multilayer reader.
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
are restored after success, failure, or cancellation. Progress always closes.

**Render Raw Passes** is a modal operation with one timer and at most one active frame render. The
sidebar publishes the current frame only after Blender reports completion and all three emitted
files pass discovery, requests a redraw, and then yields before launching the next frame. Blender
5.0.0 does not reliably preserve a parent modal operator around a nested asynchronous render, so
the extension uses a modal frame-boundary fallback: Blender remains available between frames, but
an individual frame render can temporarily block the UI.

Press **Escape** or click **Cancel** to request cancellation. The button publishes pending-cancel
feedback immediately. Escape received between frames does the same; if an `EXEC_DEFAULT` render is
blocking Blender, the OS key can remain queued while additional complete frames finish. Blender may
then deliver a render-cancel result directly, so the sidebar can move to the terminal **Cancelled**
state at the next safe boundary without visibly dwelling in the pending state. Once the extension
receives the request or render-cancel result, no later frame starts. Completed outputs are verified,
form the bounded recovery point, and are never deleted. Resolve the cause and rerun with overwrite
enabled only when replacing those raw files is intended.

Object Index remains render-engine dependent. Use Cycles for the documented Blender 5.0.0 path,
or verify that the chosen engine exposes and emits Image, Vector, and Object Index before a
production render. A missing pass fails with the pass name and inspected directory.

## Render and Process

After Object Index setup, **Render and Process** uses one modal lifecycle to run the raw renderer
and then hands its exact discovered `FramePaths` to incremental processing. It never reconstructs
raw input names for that handoff. The sidebar changes from **Rendering Raw Passes** to **Processing
Passes**, shows phase-specific and overall work, and identifies the exact phase and frame on failure.
In Blender background mode, where no window event loop can deliver modal timers, the operator uses
the equivalent synchronous composition while preserving the exact-path handoff and cleanup rules.

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
`processed/ODM_sequence_manifest.json`. Schema 5 records the frame range, ordered completed-frame
prefix, explicit resets, resolution policy, top-level History Source, and a SHA-256 semantic
fingerprint. Its readable `effective_settings` snapshot records every `FeedbackSettings` field,
matte-provider type and configuration, reset/resolution controls, and extension/Blender version
provenance. Enums use stable values; unavailable version provenance is written as `unavailable`.
The snapshot is captured once when the run starts and remains unchanged if scene controls are edited
mid-run. Top-level History Source must agree with the snapshot, and manifest replacement remains
atomic. Operational progress and cancellation state do not enter the semantic fingerprint. The
manifest contains no image data. Output from different settings, a changed range, or discontinuous
completion metadata is rejected rather than silently reused.

Processing also atomically writes the bounded schema-1 diagnostics report
`processed/ODM_processing_report.json` beside the manifest. It records the immutable configuration
and semantic-settings reference, manifest fingerprint agreement, completed prefix, terminal outcome,
reset count, and actual per-frame processing decisions. Counters cover matte/effect coverage, primary
and same-pixel history sampling, final beauty fallback, refresh restoration, historical blending, and
finite scene-linear RGB change versus current beauty. Totals exclude reset frames; detailed telemetry
is limited to the latest 96 frames and contains no image data. The recovery manifest still commits
after every completed frame, while an active diagnostics report checkpoints every 10 output frames
and may therefore lag that manifest. The report names `manifest_completed_prefix` separately from
`diagnostics_completed_prefix` and records the checkpoint interval, manifest-observation lag
separately from the diagnostics-availability gap, and each maximum while the report remains active.
Session start, first actionable near-no-op
evidence, success, cancellation, and processing failure always write a report; the first actionable
warning is logged once rather than repeated at later checkpoints, and every terminal report includes
all in-memory completed diagnostics. If terminal report persistence fails, the Blender operation's
terminal status names that report-write failure instead of silently presenting the prior checkpoint
as terminal truth. Diagnostics from a prior session are not reconstructed
on resume: the report marks that historical gap as partial or unavailable and does not claim terminal
agreement with the manifest. If an older run has no report, do not infer or fabricate diagnostics
from its processed EXRs.

A report warning is advisory and never blocks output. Efficacy assessment begins only after two
non-reset frames with non-empty target mattes. A likely near-no-op requires both actual historical
blend coverage at or below 5% of primary attempts and changed output at or below 1% of pixels (RGB
maximum absolute change above `1e-6` defines a changed pixel). Supported likely causes use inclusive
80% thresholds for mostly invalid primary samples or refresh-restored effect coverage. Empty mattes
receive their own diagnostic
instead of a generic near-no-op warning, and low persistence alone does not trigger one. The operator
status points to the report so vector convention, fallback, History Source, and refresh evidence can
be inspected without comparing EXRs manually.

**Reprocess** starts from the configured first frame. Existing outputs stop it unless **Overwrite
Processed Frames** is enabled; enabling overwrite is explicit permission to replace the complete
range. If a reprocess is interrupted, old files later in the range remain on disk but stay pending
in the manifest and are never trusted as current output. **Resume** requires a compatible
manifest, trusts complete processed output for color history, restores Hard coverage from the raw
target matte, and deterministically replays Trail effect-mask coverage across reset segments before
continuing with pending frames. Existing pending files are replaced. If recorded history
is missing, unreadable, has invalid dimensions, or otherwise violates the state contract,
**Missing History: Stop** fails without processing; **Reset** rolls the recoverable boundary back
and reprocesses from that frame with clean history. Resume never skips a gap.

**Process Existing Passes** runs as a modal, timer-driven operation. Each timer event advances at
most one complete output frame or one frame of Trail resume-history reconstruction, publishes the
phase, current frame, completed/total work, and normalized progress to the sidebar, requests a
sidebar redraw, and then yields to Blender's event loop. The Blender progress display follows the
same complete-output-frame boundaries. Blender 5.0 does not expose timer identity on modal events,
so the lifecycle gates unidentified timer events against its owned monotonic cadence and ignores
early events from unrelated timers. The initiating scene and configured range remain the run's
canonical context; configuration controls and Object Datamosh actions in every scene remain locked
until this operation finishes cleanup. Extension unload is rejected while a run is active because
Blender exposes modal-handler addition but no external handler-removal API; unregistering its class
before the handler returns would be unsafe. Cancel the run first, then disable or reload it.

Press **Escape** or click the sidebar's **Cancel** button to request cancellation. The sidebar
immediately shows **Cancel requested** while the current frame, if any, reaches its safe boundary;
no subsequent frame starts. It then shows **Cancelled**, removes the owned timer, closes progress,
and unlocks the controls. Completed files and the atomically updated manifest are retained as the
exact restart point. Choose **Resume** with the same range and settings to continue, or choose
Reprocess with overwrite when deliberately replacing the full sequence.

## Localized feedback semantics

Object Datamosh is an artistic temporal-feedback effect, not literal compressed-video bitstream
corruption. Its controls intentionally make image history unstable while keeping the workflow
scene-linear, deterministic for fixed inputs and settings, and recoverable from retained passes.

### History color versus effect coverage

**History Source chooses the pixels available for history color; Mode chooses where that history
color can affect the output.** These are independent choices:

- **Target Only** restricts history color to pixels covered by the selected-object matte. **Full
  Frame** makes the complete prior processed image available as history color.
- **Hard Localized** limits feedback to the current target matte, so output outside the current
  matte is always clean current beauty. It does not leave an effect trail.
- **Trail** carries a separate effect-coverage mask forward with motion, combines it with the
  current matte, and applies Trail Decay. Its decaying temporal coverage can extend the effect
  beyond the object's current silhouette. The mask controls *where* feedback appears; it does not
  select the history color source.

Consequently, Full Frame + Hard can place full-frame history inside the current object while still
keeping a clean image everywhere outside its matte. Full Frame + Trail can place the same color in
both the object and the decaying trail. Target Only + Trail extends only color that originated
inside selected-object coverage.

### Choosing Target Only or Full Frame

Choose **Target Only** when the moving object should preserve more correct object color, texture,
and recognizable form. Prior background and unrelated objects are excluded from its color history,
and newly revealed or invalid samples fall back to current beauty. Choose **Full Frame** for a more
extreme effect that may pull background or unrelated content into the target and its trail.

Full Frame history is recursive: after initialization, it samples the previous processed frame,
not the previous raw beauty frame. Each processed distortion can therefore feed the next frame.
Accurate motion vectors can preserve coherent structure by compensating motion successfully. To
make the result break apart more aggressively, increase Motion Quantization so block vectors move
in coarse steps and increase Diffusion to add deterministic block jitter; quantization and
diffusion deliberately break motion compensation rather than repairing the vectors.

A **background-only pre-roll** makes this difference especially visible. Begin the processing range
before the target enters the frame, then let it enter on a later frame. The first frame seeds clean
background as full-frame color history with zero target coverage. On entry, Full Frame can sample
that prior background and display it inside the new object's matte. Target Only cannot do so:
because the pre-roll target matte was empty, it has no eligible prior color there and falls back to
the entering object's current beauty. Use pre-roll as an artistic setup, not as donor-EXR history.

The **Extreme Full-Frame Feedback** guided setup is a tunable artistic default: Full Frame, Trail,
Same Screen Position fallback, Persistence `1.0`, Trail Decay `0.995`, Trail Motion Follow `0.1`,
Refresh Probability `0`, Block Size `32`, Motion Quantization `8.0`, and Diffusion `6.0`. These
starting values match the sidebar action and a deterministic moving-target fixture; tune them
for the scene rather than treating them as a physical or universal preset.

Before a long run, read the sidebar's **Active** configuration summary. The preset should show a
summary beginning `Active: Full Frame / Trail`; if the panel instead warns `Full-frame history is
OFF`, the run will use Target Only regardless of its filename or artistic intent. Controls are
snapshotted when processing starts, so this active summary remains the run configuration even if
scene controls are edited while it runs.

After processing, inspect `processed/ODM_sequence_manifest.json`. Its readable
`effective_settings` is the authoritative configuration snapshot; compare its `history_source`,
`mode`, `invalid_history_fallback`, Trail Motion Follow, and motion controls with the sidebar.
Then inspect `processed/ODM_processing_report.json` for manifest-fingerprint agreement, completion,
warnings, resets, primary/same-pixel/final-beauty sampling, refresh restoration, historical blend,
and changed-output counters. In particular, the user's old schema-v2 manifest has a top-level
`TARGET_ONLY`: that proves Target Only was active and Full Frame was never exercised. Its opaque
fingerprint cannot prove the other omitted controls, so those settings must not be guessed. Follow
the [corrected Extreme workflow migration guide](docs/extreme-workflow-migration.md) to reprocess
retained raw passes safely.

### Full Frame resets, recovery, and reprocessing

The configured first frame always seeds clean current beauty as color history and the current target
matte as coverage. An explicit reset frame does the same on that frame; it does not sample the
preceding segment. Every reset starts a new independent history segment, and multiple reset frames
are applied in ascending sequence order. Hard coverage resumes from the corresponding raw target
matte. Trail resume deterministically replays coverage from the latest reset boundary through the
completed prefix, while color history comes from the latest retained processed frame, so normal
resume preserves recursive state without rerunning completed output.

If required processed color history is absent or invalid, **Missing History: Stop** reports the
problem without continuing. **Missing History: Reset** rolls processing back to the recoverable
boundary and initializes clean history there; it does not invent or silently substitute a donor
history frame. Resolution-policy resets likewise clear both color and effect coverage.

To try Full Frame or new effect settings without rerendering the 3D scene, keep the retained raw
beauty, vector, and matte passes, select **Reprocess**, enable **Overwrite Processed Frames**, and
run **Process Existing Passes**. Changing History Source invalidates the old recovery manifest, so
start a full reprocess; the raw inputs remain reusable. Recovery manifest schema 5 retains the
canonical orientation marker (`image_orientation: display_top_left_v1`) and records complete readable
configuration provenance, including Trail Motion Follow. Processed history from older manifests is
intentionally incompatible and must be reprocessed, while retained raw passes remain reusable. This workflow is supported only while the
retained passes still satisfy the documented paths, frame range, dimensions, and matte contract.

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

A missing prior state or **Force Reset** initializes history from clean beauty. With **Target
Only**, warped history color is sampled premultiplied by its selected-object matte and is accepted
only where the sample coordinate, warped history matte, and contributing history pixels are valid.
Unselected background color therefore cannot enter Target Only history at a matte edge.

With the default **Target Only** history source, **Hard Localized** multiplies persistence by
current and warped matte coverage; refresh makes that weight zero. Newly revealed target pixels
therefore use current beauty, and premultiplied sampling prevents previous background color from
entering at matte edges.

With **Full Frame**, color is sampled directly from the complete previous processed frame rather
than being premultiplied by or restricted to the previous target matte. In Hard Localized mode, the
current target matte controls where feedback appears and becomes the next effect-mask history.

In **Full Frame + Trail**, history matte is instead an independent effect/output coverage mask.
**Trail Motion Follow** blends its prior screen-space coverage with coverage warped by current
motion, then **Trail Decay** is applied and current target coverage is reinforced using a clamped
maximum. That combined mask controls only
where the independently sampled full-frame color appears, allowing trails to contain background or
unrelated content from the prior processed frame. Invalid or out-of-bounds color falls back to
current beauty; invalid or out-of-bounds mask samples do not propagate old effect coverage. Each
next state stores the complete processed output as color history and the combined effect mask as
mask history.

First frames and resets initialize complete color history from current beauty and mask history from
the current target matte. An object visible on that first/reset frame therefore seeds its clean
image; a background-only pre-roll can instead produce a more corrupted entrance when the object
appears. **History Source** is available in the sidebar as **Target Only (Legacy / Stable)** and
**Full Frame (Extreme)**. Changing it invalidates a processed recovery manifest but leaves retained
raw beauty, vector, and matte passes reusable for a new reprocess run.

The sidebar's **Extreme Full-Frame Feedback** action applies this documented artistic starting
point: Full Frame, Trail, Same Screen Position fallback, persistence `1.0`, Trail Decay `0.995`,
Trail Motion Follow `0.1`, Refresh Probability `0`, Block Size `32`, Motion Quantization `8.0`, and
Diffusion `6.0`. These values are within the controls'
normal ranges and intentionally strong, but they do not guarantee identical visual results across
scenes. The action changes only those Object Datamosh effect settings (plus its status report); it
does not alter target, camera, render, output, color-management, material, collection, or visibility
state.

**Trail** with Target Only advects selected-object history with the same motion field, multiplies
warped history coverage by **Trail Decay**, and combines that coverage with the current matte for
the next frame.
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
data, or returned immutable values; there is no mutable module-level runtime state. The focused `ExistingPassModalController` and `RawRenderModalController` own their incremental
event state machines. `BlenderRenderAdapter` isolates frame launch and scene/run-identity-checked
render observation, while the reusable `ModalOperationLifecycle` owns one modal timer, Blender
progress, safe sidebar redraws, operation
locking, cancellation requests, and idempotent universal cleanup with a separate workflow cleanup
hook. Blender properties contain only transient, `SKIP_SAVE` run metadata—never either runtime
service—so reopening a blend cannot resurrect an active lock without its controller or timer. The
active controller is referenced under an `ODM_` key in Blender's transient driver namespace until
cleanup, keeping the global lock and Cancel action reachable if the initiating scene is switched or
removed without introducing mutable module-level state.
Setup and cleanup never delete, disconnect, or replace unrelated
compositor nodes and restore pass settings that they change.

## Development and verification

Verify that the committed lockfile matches the project metadata, then install the development
environment:

```bash
uv lock --check
uv sync
```

Keep the editable `object-datamosh` package version in `uv.lock` synchronized with the
`[project].version` value in `pyproject.toml`; routine `uv` commands should not rewrite the tracked
lockfile.

Set `BLENDER_BIN` to the tested Blender executable, then run all repository gates:

```bash
uv run ty check
uv run pytest -q
uv run ruff check .
"$BLENDER_BIN" --background --factory-startup --python tests/blender_smoke_test.py
"$BLENDER_BIN" --command extension validate src/object_datamosh
mkdir -p dist
"$BLENDER_BIN" --command extension build \
  --source-dir src/object_datamosh \
  --output-dir dist
```

The development-only Blender 5.0 stubs support static checking outside Blender. NumPy is a
development dependency and is bundled with Blender at runtime; the extension declares no
third-party runtime dependency. The current foreground observations, gate results, archive path,
and checksum are recorded in
[Responsive operations release verification](docs/responsive-operations-release-verification.md).
The integrated Full Frame commands, artifact, and remaining visual checks are recorded in
[Full Frame release verification](docs/full-frame-release-verification.md). Corrected workflow
migration is documented in the [migration guide](docs/extreme-workflow-migration.md), and its
orientation, provenance, fallback, Trail, preset, and diagnostics changes are summarized in the
[0.3.0 release notes](docs/release-notes-0.3.0.md).

## Performance expectations

The feedback core processes complete frames in memory and is single-process NumPy code; Blender
rendering and OpenEXR reads/writes are separate costs. As a reference measurement, three warm
hard-localized feedback frames at 1280×720, block size 16, and zero motion took 0.191, 0.171, and
0.160 seconds (0.171-second median) on an Apple M3 Max with 36 GB RAM, Python 3.12.8, and NumPy
2.5.1. This synthetic measurement was recorded on 2026-07-18 with `process_frame` directly and
excludes image I/O. Production time varies with resolution, storage, compositor complexity, render
engine, and scene complexity; measure a representative frame range before scheduling a final
render. Memory use scales with pixel count because source, motion, matte, sampling, and history
arrays coexist during processing. Motion reduction, quantization, diffusion, and refresh selection
remain compact block grids until `process_frame` expands them for pixel sampling and blending.

### Reproducible Extreme benchmark

Run the production-shaped 1920×1080 benchmark with the tested Blender executable:

```bash
"$BLENDER_BIN" --background --factory-startup --python scripts/benchmark_extreme.py -- \
  --warmups 1 --measured 3 --output docs/evidence/extreme-benchmark-baseline.json
```

The script creates deterministic float32 beauty, Vector, matte, and history arrays, writes its EXR
fixtures under a temporary directory, uses `extreme_full_frame_feedback_settings()`, and exercises a
three-frame strictly sequential sequence containing non-reset Full Frame Trail frames. Its JSON
separates pure-core, individual EXR reads, processed EXR write, and complete sequential timings; it
records environment metadata, warm-up and measured counts, median/minimum/maximum values, largest
stages, and a median-based 147-frame extrapolation. Results are observational developer evidence,
not a production threshold. The current baseline is committed at
[`docs/evidence/extreme-benchmark-baseline.json`](docs/evidence/extreme-benchmark-baseline.json).

The custom-reader-first routing comparison uses the same benchmark with two warm-ups and seven
measurements. On Blender 5.2.0 LTS on arm64 macOS, removing the old Blender probe from the
three-pass multilayer route reduced its median from 426.56 ms to 419.68 ms (1.61%, 1.016×), with
zero temporary Images on the supported path and bit-identical ZIP/ZIPS fixtures. The evidence also
records the unfavorable regular-EXR comparison: Blender's native Image decoder is much faster than
the narrow NumPy decoder on this machine. See
[`docs/evidence/issue-76-custom-exr-routing.json`](docs/evidence/issue-76-custom-exr-routing.json).
These figures are developer evidence, not a claim about another machine.

Benchmark the separate diagnostics checkpoint policy with:

```bash
uv run python scripts/benchmark_diagnostics_reports.py --warmups 1 --measured 3 \
  --output docs/evidence/issue-74-diagnostics-checkpoint.json
```

The Full Frame clean-history sampling optimization can be reproduced from separate base and head
worktrees. The runner rejects a worktree whose `feedback.py` does not match the requested side:

```bash
ISSUE75_RUNNER=$PWD
ISSUE75_HEAD=/tmp/object-datamosh-issue75-head
ISSUE75_BASE=/tmp/object-datamosh-issue75-base
git worktree add --detach "$ISSUE75_HEAD" 8220a56f4284969ca4f1270aad4fa64a76e926a5
git worktree add --detach "$ISSUE75_BASE" 0d98fb67fffd9b24cdd32ac053541268d6a25511
UV_FROZEN=1 uv run --frozen python "$ISSUE75_RUNNER/scripts/benchmark_full_frame_sampling.py" \
  --revision after --source-root "$ISSUE75_HEAD" --warmups 1 --measured 5 \
  --output /tmp/issue75-after.json
UV_FROZEN=1 uv run --frozen python "$ISSUE75_RUNNER/scripts/benchmark_full_frame_sampling.py" \
  --revision before --source-root "$ISSUE75_BASE" --warmups 1 --measured 5 \
  --output /tmp/issue75-before.json
uv run python scripts/benchmark_full_frame_sampling.py \
  --compare-before /tmp/issue75-before.json --compare-after /tmp/issue75-after.json \
  --output /tmp/issue75-comparison.json
```

The same-machine before/after evidence in
[`docs/evidence/issue-75-full-frame-sampling.json`](docs/evidence/issue-75-full-frame-sampling.json)
uses deterministic 1920×1080 float32 Extreme input. Median total core processing fell from
665.34 ms to 338.76 ms (49.08%, 1.96×); processed RGBA, next state, effect coverage, and diagnostics
were bit-identical. Stage figures are developer evidence, not CI timing gates or claims about other
machines.

The committed same-machine synthetic 147-frame result reduced atomic report writes from 295 to 31
(89.49%). Median JSON construction fell from 147.05 ms to 15.48 ms, atomic-write batches from
143.77 ms to 13.09 ms, and total synthetic report sequence overhead from 199.88 ms to 20.69 ms.
The benchmark uses temporary outputs and does not alter the unchanged per-frame recovery-manifest
cadence. See
[`docs/evidence/issue-74-diagnostics-checkpoint.json`](docs/evidence/issue-74-diagnostics-checkpoint.json).
The PERF-1 benchmark was also rerun with the production-shaped Blender command:

```bash
"$BLENDER_BIN" --background --factory-startup --python scripts/benchmark_extreme.py -- \
  --warmups 1 --measured 3 --output docs/evidence/issue-74-extreme-rerun.json
```

On the same machine and methodology as the committed PERF-1 baseline (one warm-up, three
measurements), median complete sequential processing was
1.433 s for its three-frame fixture versus 4.271 s in the original baseline (66.45% cumulative
improvement across the performance roadmap). This short run is a rerunability check, not an isolated
measurement of checkpointing; the 147-frame synthetic benchmark above isolates report cadence.

Each processing report also has a schema-v1 `performance` section. It records nanosecond timings for
beauty, Vector, and matte reads; core processing; processed EXR write; atomic manifest commit;
diagnostics-report commit; and total frame time. `observational_only` is true, and timing history is
bounded to the latest 96 frames. Timing data is excluded from semantic settings, fingerprints, and
resume compatibility and does not change frame diagnostics or image output.

## Troubleshooting

For an ineffective or unexpected Extreme result, preserve the manifest and processing report before
reprocessing. Ratios describe this run's measured samples; they diagnose likely causes but do not
promise a particular artistic result.

| Symptom | Likely causes and evidence to inspect | Corrective action |
| --- | --- | --- |
| Output upside down | A pass was converted outside the supported boundary or a noncanonical reader was used. Check manifest `image_orientation` is `display_top_left_v1` and compare asymmetric corner markers in raw and processed EXRs. | Use the documented pass layout and Blender image I/O; remove extra vertical flips. Calibrate rather than negating Vector Y to compensate for an image flip. |
| Object looks almost clean | Target Only, successful motion compensation, low historical blend, or refresh may preserve clean beauty. Check the **Active** summary, `effective_settings`, report warnings, `historical_blend_ratio`, `changed_output_ratio`, and refresh counters. | Select Full Frame or apply the Extreme preset before processing; calibrate motion, then tune quantization/diffusion. Reprocess retained raw passes with explicit processed overwrite. |
| Manifest says `TARGET_ONLY` | Target Only was active; in a schema-v2 manifest the top-level value proves the run never exercised Full Frame, while its opaque fingerprint proves nothing about omitted controls. | Select Full Frame or apply the preset, verify `Active: Full Frame / Trail`, and perform a full Reprocess. Do not Resume or guess schema-v2 settings. |
| Most primary history samples are out of bounds | Motion channels, sign, Y orientation, gain, or scale may be wrong, or motion may legitimately leave frame. Check `primary_history_invalid_samples`/ratio and same-pixel/final-beauty fallback counters. | Run vector calibration for the Blender version and engine; correct RG/BA, Reverse, Flip X/Y, or Gain. Same Screen Position can preserve history but does not repair vectors. |
| Trail follows object rather than remaining behind | Trail Motion Follow is near `1`, the compatibility behavior. Check `effective_settings.trail_motion_mix` and confirm Full Frame + Trail. | Move Trail Motion Follow toward `0` for screen-space persistence, or choose an intermediate mix; reprocess to compare. |
| Output is unchanged outside Hard matte | This is expected Hard Localized behavior, not evidence that Full Frame is off. Check `effective_settings.mode` and outside-matte change evidence. | Choose Trail if decaying effect coverage should extend outside the current matte; Full Frame changes available history color, not Hard's effect boundary. |

- **No output or a missing pass:** run **Setup Object Index** for the current target and view layer,
  use an engine that emits Image, Vector, and Object Index (Cycles is the tested path), and inspect
  the pass name and directory in the reported error.
- **Existing output blocks a run:** enable the matching overwrite control only when replacement is
  intentional. To continue compatible processed output, select **Resume** instead; the extension
  never deletes old files automatically.
- **Resume rejects a sequence:** keep the same range, matte provider, and feedback settings. If
  history is missing or invalid, choose the explicit missing-history **Reset** policy or start a
  full **Reprocess** with overwrite enabled.
- **Effect moves the wrong way or by the wrong amount:** use the vector calibration scene to verify
  RG versus BA, reversal, axis flips, and gain for the active Blender version and engine.
- **Unsaved-file warning or unexpected root:** save the blend file or choose an explicit absolute
  output directory. Blender-relative paths require a saved blend file as their anchor.
- **Cryptomatte fails:** select Object Index or a numbered external matte sequence. Cryptomatte
  decoding is intentionally not implemented in the MVP.

## Historical release verification record (before issue #23)

This inventory is retained as the pre-modal baseline; it does not describe the current PR archive.
The production gate was run on 2026-07-18 with Blender 5.0.0. `uv run ty check` passed; the pure
suite reported 121 passed and one Blender-runtime skip; and the factory-startup Blender smoke test
printed `Object Datamosh Blender smoke test passed` (1.12 seconds wall time for its tiny fixtures).
Manifest validation succeeded, and Blender built `dist/object_datamosh-0.1.0.zip` (31,094 bytes,
SHA-256 `f15d92386176847b66a0c6f6e859de38577f17f2b52d86d2181c3621ac46a022`). Archive inspection
found the manifest, 8 top-level Python modules, and 9 `core/` Python modules, with no caches,
tests, development dependencies, or compiled third-party libraries. Issue #10 changed only this
README; the generated ZIP remains ignored under `dist/`. Visual node layout, sidebar
polish, interactive cancellation, calibration interpretation, and foreground control behavior
remain explicit interactive checks; they were not claimed by the background gate.

## Current limitations

- Resume is deliberately range-based and sequential. It does not splice arbitrary processed
  fragments, migrate old manifest schemas, or delete stale files; incompatible or discontinuous
  runs require explicit full-range reprocessing.
- Trail mode follows only the available matte and vector information. Occlusions, disocclusions,
  inaccurate vectors, and low-resolution mattes can shorten or distort trails; it does not infer
  hidden geometry. Target Only trails cannot admit unrelated-object/background history, while Full
  Frame trails intentionally can sample such content from the complete prior processed frame.
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
  sequences, processed EXR contracts, hard-localized output, trail controls, processed-output
  collision refusal, deterministic modal event boundaries, and real registered-operator dispatch
  through Blender's window manager. Background Blender does not pump foreground modal events while
  the smoke script owns the main thread, so deterministic timer advancement/final cleanup use a
  recorded window-manager boundary. Foreground modal dispatch, visible per-frame sidebar redraws,
  Escape and Cancel-button boundaries, Resume, cleanup, and immediate restart were separately
  verified in Blender 5.0.0 and are recorded in the
  [responsive operations release report](docs/responsive-operations-release-verification.md).
  Visual node layout, broader sidebar polish, and calibration interpretation (especially Y-axis and
  reversed-motion checks) remain manual production-scene checks.
