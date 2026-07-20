# Full Frame release verification

Date: 2026-07-20

Issue: [#48 — Finalize Full Frame documentation and release validation](https://github.com/cmoyates/object-datamosh/issues/48)

Implementation base: `3ed7165e71006f29531653a3e89395bb3402f0d3`

Configured Blender: Blender 5.2.0 LTS (`fbe6228777e7`, built 2026-07-14)

## Result

The combined Full Frame implementation passed the complete repository static, pure-Python,
background-Blender, extension-validation, and packaging matrix available in the configured
environment. The issue #48 change itself is limited to workflow documentation, documentation tests,
and synchronization of the existing project version in `uv.lock`; it does not change extension
runtime code.

Both repository-documented background Blender checks were available and passed. No prescribed
background check was skipped for an environment limitation. The pure-Python suite's single skip is
intentional: collection outside Blender skips `tests/blender_smoke_test.py`, which was then run
separately with Blender and passed.

## Commands and results

Commands were run from the repository root with `BLENDER_BIN` pointing to the configured Blender
5.2.0 LTS executable.

| Command | Result |
|---|---|
| `uv run ty check` | Passed: `All checks passed!` |
| `uv run pytest -q` | Passed: 262 tests; 1 Blender-runtime collection skip outside Blender |
| `uv run ruff check .` | Passed: `All checks passed!` |
| `"$BLENDER_BIN" --background --factory-startup --python tests/blender_smoke_test.py` | Passed: `Object Datamosh Blender smoke test passed`; includes the imported raw-render, processing, and combined modal scenarios plus isolated registered-operator dispatch |
| `"$BLENDER_BIN" --background --factory-startup --python tests/create_calibration_scene.py` | Passed: created the owned calibration scene, rectangle, camera, and expected `(-2, 0, 0)` to `(2, 0, 0)` motion |
| `"$BLENDER_BIN" --command extension validate src/object_datamosh` | Passed: `Success parsing TOML in "src/object_datamosh"` |
| `mkdir -p dist` followed by `"$BLENDER_BIN" --command extension build --source-dir src/object_datamosh --output-dir dist` | Passed: created `dist/object_datamosh-0.1.0.zip` |
| `shasum -a 256 dist/object_datamosh-0.1.0.zip` | Passed: SHA-256 `86c5c0a40e402d55bbbb5390a2bd89ec3b52aa99708ef1d677eac5ed969e383e` |
| `unzip -l dist/object_datamosh-0.1.0.zip` plus a forbidden-entry scan | Passed: 27 entries, 55,375 bytes; no tests, caches, build trees, virtual environments, compiled libraries, or compiled Python files |

The installation artifact is therefore:

- path: `dist/object_datamosh-0.1.0.zip` (ignored build output, relative to the repository root);
- size: 55,375 bytes;
- SHA-256: `86c5c0a40e402d55bbbb5390a2bd89ec3b52aa99708ef1d677eac5ed969e383e`.

## Scope confirmation

The release retains the NumPy CPU processing architecture and Blender image-sequence workflow.
Issue #48 introduces no GPU processing, shader work, backend abstraction, donor EXR history,
compiled runtime dependency, render-pass redesign, or compositor feedback loop. The extension
still has no required third-party runtime dependency; Blender's bundled Python and NumPy remain the
runtime foundation.

## Remaining interactive judgment

The automated checks establish contracts, deterministic processing, registration, generated pass
and processed files, background modal boundaries, extension validity, and packaging. They do not
establish whether a particular Full Frame result is artistically desirable. The following still
require interactive Blender judgment in representative production scenes:

- recognizable-form loss and the desired balance among persistence, quantization, diffusion,
  refresh, and trail decay;
- motion-vector channel, sign, scale, Y-axis, and reverse-motion calibration for the chosen engine
  and Blender build;
- trail shape around real occlusions, disocclusions, fine mattes, and frame edges;
- node layout, sidebar polish, color-management choices, and final compositor integration; and
- foreground UI redraw and cancellation feel. Blender can still block the UI during an individual
  raw frame render, as documented in the responsive-operations release report.

Object Datamosh remains an artistic recursive temporal-feedback effect, not literal compressed-video
bitstream corruption. Background validation cannot replace visual approval of that effect.
