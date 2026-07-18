# Object Datamosh repository instructions

## Project

This repository contains a modern Blender extension named Object Datamosh.

The installable extension package is located at:

src/object_datamosh

## Blender compatibility

- Use the modern blender_manifest.toml extension format.
- Do not add a legacy bl_info dictionary.
- Target the Blender executable specified by the BLENDER_BIN environment variable.
- Inspect the installed Blender Python API instead of guessing API names.
- Where an API varies between Blender releases, use feature detection with hasattr.
- Set blender_version_min to the oldest version actually tested.

## Architecture

- Keep Blender-specific code separate from the image-processing core.
- Modules under src/object_datamosh/core must not import bpy.
- The core should operate on NumPy float32 arrays.
- Use relative imports inside the extension.
- Avoid mutable module-level runtime state.
- Do not call bpy APIs from background threads.

## Scene safety

- Do not delete, disconnect, or replace existing user compositor nodes.
- Do not permanently overwrite materials, collections, object visibility, render settings, or output paths.
- Prefix data created by the extension with ODM\_.
- Tag extension-created nodes and data so they can be identified reliably.
- Setup and cleanup operators must be idempotent.
- Restore changed scene settings when cleanup is requested.
- Never delete output files without a separate explicit user action.

## Dependencies

- The MVP must have no required runtime dependency beyond Blender's bundled Python modules and NumPy.
- Do not add OpenImageIO, OpenEXR, PyTorch, OpenCV, or another compiled dependency to the MVP.
- Put image I/O behind an interface so another backend can be added later.

## Quality

- Use type hints where practical.
- Give operators useful poll methods and clear error messages.
- Use Blender's report method for user-visible failures.
- Log enough information to diagnose pass names, channel mappings, paths, and frame numbers.
- Ensure register and unregister can run repeatedly without errors.

## Verification

After relevant changes:

1. Run the pure Python unit tests.
2. Run the Blender background smoke test.
3. Validate the Blender extension.
4. Build the installation ZIP.
5. Report the commands run and their results.

Do not claim that a feature works unless it was tested, or clearly identify what still requires an interactive Blender test.

## Agent skills

### Issue tracker

Issues and PRDs are tracked in this repository's GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Triage uses the five default canonical labels. See `docs/agents/triage-labels.md`.

### Domain docs

Domain documentation uses a single-context layout. See `docs/agents/domain.md`.
