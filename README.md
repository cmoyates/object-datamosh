# Object Datamosh

Object Datamosh is a modern Blender extension for building object-localized temporal feedback
workflows. The current MVP targets and has been tested with **Blender 5.0.0**. It provides the
user interface, shared contracts, pure NumPy hard-localized feedback core, and a non-destructive
Object Index compositor setup; it does not yet render or process full image sequences.

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
- the active view-layer name;
- sequence start/end frames and an optional output-directory override;
- Object Index, External Matte, and experimental Cryptomatte source choices;
- persistence, block size, motion-channel/direction/axis/gain/clamp/quantization, diffusion,
  refresh-probability, and deterministic-seed controls; and
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
  data-blocks. Matte files use scalar coverage from the EXR red channel; `read_mask` returns that
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

## Hard-localized feedback semantics

Motion channels contain a forward displacement `(x, y)` from a history pixel to its location in
the current frame. For a current pixel `(x, y)`, the processor therefore samples history at
`(x - displacement_x, y - displacement_y)`. RG maps R to X and G to Y; BA maps B to X and A to Y.
**Reverse Motion** negates both components, while **Flip X** and **Flip Y** negate individual axes.
These overrides are intentionally exposed because pass conventions must be checked with the
manual calibration workflow planned for a later ticket.

The processor applies motion gain, direction/axis overrides, and a direction-preserving magnitude
clamp. It computes a current-matte-weighted mean vector for each block, including partial blocks at
odd image edges, then expands that representative vector over the block. A positive quantization
value rounds each component to the nearest multiple of that value; zero disables quantization.
Diffusion adds an independent per-block X/Y offset in `[-Diffusion, +Diffusion]`. Refresh selects
whole blocks to use clean beauty. Both choices are deterministic hashes of seed, frame number, and
block coordinates and do not touch NumPy's global random state.

A missing prior state or **Force Reset** initializes history from clean beauty. Otherwise, warped
history color is sampled premultiplied by its selected-object matte and is accepted only where the
sample coordinate and warped history matte are valid. Persistence is multiplied by current and
warped matte coverage; refresh makes that weight zero. Consequently pixels outside the current
matte equal clean beauty exactly, and unselected background color cannot enter history at a matte
edge. This is hard localization only: selected-object trails beyond the current silhouette are not
implemented yet.

All inputs are finite NumPy `float32` arrays. Beauty and motion are `(height, width, 4)`; matte is
`(height, width)` coverage in `[0, 1]`. Processing is sequential: pass the returned state to the
next frame, or request a reset when history must be discarded.

## Architecture

```text
Blender sidebar / operators
        │
        ├── Object Index setup/restore ───────────────── owned compositor nodes
        ├── SequencePaths + matte-provider contracts ── future render/process services
        │
        ├── FeedbackSettings / FeedbackState ────────── NumPy feedback core
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

- Full frame-range raw rendering and sequence processing are intentionally deferred to subsequent
  implementation tickets. The pure frame processor does not read or write image files.
- Hard-localized mode cannot leave history outside the current selected-object silhouette. Trail
  mode and sequence-level reset/recovery policy are deferred to later tickets.
- Object Index is the MVP selected-object matte. External mattes follow the documented
  numbered-file contract. Cryptomatte appears as experimental UI/contract surface only; decoding
  is not implemented. Object Index availability remains render-engine dependent.
- The background smoke test verifies registration, the complete emitted sidebar control surface,
  target assignment and status, path derivation, setup idempotency, cleanup/restoration, unrelated
  node survival, a tiny Cycles beauty/vector/matte render, float EXR round-tripping, temporary image
  cleanup, and render-setting restoration. Visual node layout, sidebar polish, and interactive
  control behavior still require a manual foreground Blender check.
