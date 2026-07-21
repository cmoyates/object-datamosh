# Corrected Extreme Full Frame release verification

Date: 2026-07-20

Issue: [#61 — Document, migrate, and release-validate the corrected Extreme workflow](https://github.com/cmoyates/object-datamosh/issues/61)

Implementation base: `fc803d173c02c91d80257836ea7e5f76ec3c64aa`

Configured Blender: Blender 5.2.0 LTS (`fbe6228777e7`, built 2026-07-14)

## Result

Every repository-prescribed automated release gate passed in the configured environment. The
background Blender smoke included the actual Blender image-I/O path and complete synthetic Extreme
A–D sequence: a 65×37 asymmetric four-frame raw beauty/Vector/matte fixture, eight processed EXRs
across Trail and Hard companions, two schema-5 manifests, and two processing reports. It verified
orientation markers, Full Frame provenance, Same Pixel History fallback, recursive frame history,
screen-space Trail outside the current matte, Hard's clean outside-matte contract, diagnostics,
temporary image cleanup, and scene-setting restoration.

The pure suite's one skip is intentional: pytest outside Blender skips collection of
`tests/blender_smoke_test.py`; that script was then run with Blender and passed. No prescribed check
was unavailable. Full Blender background automation does not establish artistic quality or the
interactive visual checks listed below.

## Commands and compact results

All commands ran from the repository root with `BLENDER_BIN` set to the configured Blender
executable. Complete output is retained in the issue workflow's ignored `.git` log directory; this
record contains stable command/result references only.

| Command | Result |
|---|---|
| `uv lock --check` | Passed; resolved 12 packages; tracked lockfile consistent. |
| `uv sync` | Passed; resolved 12 and audited 10 packages; no tracked lockfile change. |
| `uv run ty check` | Passed: `All checks passed!`. |
| `uv run pytest -q` | Passed: 316 passed, 1 intentional Blender-runtime skip, 7.26 s. |
| `uv run ruff check .` | Passed: `All checks passed!`. |
| `"$BLENDER_BIN" --version` | Passed: Blender 5.2.0 LTS, hash `fbe6228777e7`. |
| `"$BLENDER_BIN" --background --factory-startup --python tests/blender_smoke_test.py` | Passed: `Object Datamosh Blender smoke test passed`; Extreme fixture receipt reported 65×37, four raw frames, eight processed EXRs, two manifests, and two reports. |
| `"$BLENDER_BIN" --background --factory-startup --python tests/create_calibration_scene.py` | Passed: owned calibration scene, rectangle, and camera created with expected `(-2, 0, 0)` → `(2, 0, 0)` motion. |
| `"$BLENDER_BIN" --command extension validate src/object_datamosh` | Passed: `Success parsing TOML in "src/object_datamosh"`. |
| `mkdir -p dist` then `"$BLENDER_BIN" --command extension build --source-dir src/object_datamosh --output-dir dist` | Passed: created `dist/object_datamosh-0.2.0.zip`, 64,058 bytes. |
| `shasum -a 256 dist/object_datamosh-0.2.0.zip` | Passed: `3718a4414ed3b81d419359f96096cecb8b52be8083ce46c0e87257bd82c8e701`. |
| `unzip -l dist/object_datamosh-0.2.0.zip` and a Python forbidden-entry audit | Passed: 29 entries, 245,131 uncompressed bytes; no tests, caches, virtual/build trees, compiled libraries, or compiled Python. |
| `git diff --check` | Passed. |

The installation artifact is ignored build output and is not committed:

- path: `dist/object_datamosh-0.2.0.zip`;
- package manifest version: `0.2.0`;
- size: 64,058 bytes;
- SHA-256: `3718a4414ed3b81d419359f96096cecb8b52be8083ce46c0e87257bd82c8e701`.

The Blender extension manifest, repository project metadata, release notes, and lockfile editable
package entry now agree on version `0.4.0`.

## Migration and compatibility cases verified

- Pure and Blender smoke coverage reject a schema-v2 manifest with top-level `TARGET_ONLY` rather
  than guessing omitted settings or trusting its processed file; all raw beauty, Vector, and matte
  files remain untouched and reusable.
- Schema 5 requires canonical `display_top_left_v1` orientation and exact readable
  `effective_settings`; top-level History Source must agree with that snapshot.
- Added manifest and report fields retain strict semantic fingerprint, range, reset, provider,
  resolution-policy, provenance, and contiguous-prefix checks.
- Default and migrated scene controls remain Target Only / Hard Localized / Current Beauty, while
  the Extreme action explicitly opts into Full Frame / Trail / Same Screen Position.
- Reset frames and missing-history policy remain conservative; resume never skips a gap or invents
  history, and explicit reprocessing is protected by **Overwrite Processed Frames**.

User-facing steps are in the [migration guide](extreme-workflow-migration.md). Orientation,
provenance, fallback, Trail propagation, retuned preset values, and diagnostics are recorded in the
[0.4.0 release notes](release-notes-0.4.0.md).

## Scope and remaining interactive limitations

This batch added no GPU/backend work and no compiled runtime dependency. It adds no shader,
CUDA/CuPy/PyTorch/OpenCV, codec corruption, donor history, render-pass redesign, or compositor
feedback loop. The implementation remains Blender plus bundled NumPy on the CPU.

Background tests cannot decide whether an Extreme sequence is artistically desirable. Interactive
checks still required for each production scene are:

- RG versus BA, sign, gain, Y-axis orientation, and reverse-motion interpretation for the exact
  Blender build, engine, camera, and motion;
- trail shape around real occlusions, disocclusions, fine mattes, and frame edges;
- recognizable-form loss and tuning of persistence, quantization, diffusion, decay, and motion
  follow;
- sidebar/node layout, color-management choices, and compositor integration; and
- foreground redraw, Escape/Cancel feel, and UI blocking during an individual raw frame render.

No interactive visual production-scene review was performed or claimed by issue #61.
