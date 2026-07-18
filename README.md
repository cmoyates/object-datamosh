# Object Datamosh

Object Datamosh is a modern Blender extension for building object-localized temporal feedback
workflows. The current MVP targets and has been tested with **Blender 5.0.0**. It provides the
user interface, shared contracts, and pure NumPy hard-localized feedback core; it does not yet
configure compositor passes or render and process image sequences.

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

- a target-object picker and **Use Active Object** action;
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
        ├── SequencePaths + matte-provider contracts ── future render/process services
        │
        ├── FeedbackSettings / FeedbackState ────────── NumPy feedback core
        │
        └── ImageSequenceIO ─────────────────────────── BlenderImageIO (bpy)

object_datamosh.core: NumPy + Python only; never imports bpy
```

No background thread calls Blender APIs. Runtime state belongs to Blender scenes or returned
immutable values; there is no mutable module-level runtime state. The extension does not delete,
disconnect, or replace user compositor nodes and does not permanently alter render settings.

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

- Compositor setup, raw rendering, and sequence processing are intentionally deferred to
  subsequent implementation tickets. The pure frame processor does not read or write image files.
- Hard-localized mode cannot leave history outside the current selected-object silhouette. Trail
  mode and sequence-level reset/recovery policy are deferred to later tickets.
- Object Index is the planned MVP selected-object matte. External mattes follow the documented
  numbered-file contract. Cryptomatte appears as experimental UI/contract surface only; decoding
  is not implemented.
- The background smoke test verifies registration, the complete emitted sidebar control surface,
  target assignment and status, path derivation, float EXR round-tripping, temporary image cleanup,
  and render-setting restoration. Visual polish and interactive control behavior still require a
  manual foreground Blender check.
