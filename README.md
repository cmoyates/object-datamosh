# Object Datamosh

Object Datamosh is a modern Blender extension for building object-localized temporal feedback
workflows. The current MVP shell targets and has been tested with **Blender 5.0.0**. It establishes
the user interface and shared contracts that rendering and feedback processing will use; it does
not yet configure compositor passes, render sequences, or process feedback.

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
anchor. For an unsaved blend file, Object Datamosh falls back to Blender's temporary directory at
`ODM_object_datamosh_unsaved` and displays this warning:

> Save the blend file to use a project-relative output directory.

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
- **Feedback settings:** immutable `FeedbackSettings` contains all sidebar feedback controls and
  validates probabilities, block size, and non-negative motion controls.
- **Matte providers:** `ObjectIndexMatteProvider` resolves rendered Object Index mattes;
  `ExternalMatteProvider` resolves a numbered external sequence. The
  `CryptomatteMatteProvider` contract intentionally fails with a clear `NotImplementedError`:
  decoding remains experimental and is not part of the MVP.
- **Image I/O:** `ImageSequenceIO` is the processing boundary. `BlenderImageIO` is its Blender
  implementation and reads/writes full-float RGBA OpenEXR using temporary `ODM_` Image
  data-blocks. It removes those data-blocks and restores temporary render image settings in
  `finally` paths.
- **Ownership:** extension-created data uses the `ODM_` prefix and the
  `object_datamosh_owned` custom-property tag. Helpers live in
  `object_datamosh.core.ownership`.

`object_datamosh.ui.feedback_settings_for_scene` copies Blender properties into the pure settings
contract, preventing Blender-facing services from redefining feedback options.

## Architecture

```text
Blender sidebar / operators
        │
        ├── SequencePaths + matte-provider contracts ── future render/process services
        │
        ├── FeedbackSettings / FeedbackState ────────── future NumPy feedback core
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

- Compositor setup, raw rendering, temporal feedback mathematics, and sequence processing are
  intentionally deferred to subsequent implementation tickets.
- Object Index is the planned MVP selected-object matte. External mattes follow the documented
  numbered-file contract. Cryptomatte appears as experimental UI/contract surface only; decoding
  is not implemented.
- The background smoke test verifies registration, the complete emitted sidebar control surface,
  target assignment and status, path derivation, float EXR round-tripping, temporary image cleanup,
  and render-setting restoration. Visual polish and interactive control behavior still require a
  manual foreground Blender check.
