# Responsive operations release verification

Date: 2026-07-19

Release: Object Datamosh 0.1.0

Platform: Blender 5.0.0 (`a37564c4df7a`), macOS, Apple Silicon

Issue: [#26 — Verify and release responsive operations](https://github.com/cmoyates/object-datamosh/issues/26)

## Result

The responsive modal operations passed the pure-Python, static-analysis, Blender background,
extension-validation, packaging, and foreground Blender checks listed below. No implementation
defect was found, so this release-verification change is documentation-only.

The foreground checks used an actual Blender window and window-manager event loop, not
`--background`. The fixture rendered a 32×24 Cycles scene at one sample so ten complete frames
could be checked quickly. It observed scene-owned runtime values and an instrumented visible 3D
View sidebar panel at every redraw. A real macOS Escape key event was sent to the foreground
Blender application through System Events; the Cancel-button checks invoked the registered public
operator used by the sidebar button.

## Foreground Blender 5.0.0 observations

### Successful ten-frame Render and Process

- Invocation returned `RUNNING_MODAL`; the visible sidebar observer drew all rendering work
  boundaries 0–9 followed by all processing work boundaries 10–19, and the runtime then reached
  its terminal 20/20 state.
- The phase visibly changed from `Rendering Raw Passes` to `Processing Passes` without mouse
  movement. Current-frame, phase-work, overall-work, and normalized-progress values advanced at
  each complete-frame boundary.
- Ten beauty, Vector, Object Index matte, and processed EXR files were present after completion.
- Runtime ended inactive at `COMPLETED`, with 20/20 work and progress 1.0. The original scene frame
  (7) was restored.
- A second operation returned `RUNNING_MODAL` immediately after completion.

### Cancellation during raw rendering

Two separate runs covered both user inputs:

- The sidebar **Cancel** button was invoked after raw frame 1. The runtime immediately displayed
  `Cancel requested; waiting for a safe boundary...`, remained active while cancellation was
  pending, then ended inactive at `CANCELLED`. Frame 1's three raw files remained; frame 10 was not
  created.
- A real **Escape** key event was delivered during a 100-frame raw phase. The run stopped after its
  contiguous two-frame prefix, displayed the pending and terminal cancellation states, retained
  those completed raw files, and did not create frame 100.
- Both runs restored scene frame 7. The owned render-complete/render-cancel handler counts returned
  to their baseline, and another operation started immediately, demonstrating that the modal timer,
  handlers, and operation lock no longer owned an active run.

### Cancellation and Resume during existing-pass processing

- **Process Existing Passes** was cancelled with the sidebar button after two complete frames. The
  pending status appeared immediately, no third frame started, and the run ended inactive at
  `CANCELLED` with progress 2/10.
- Processed frames 1–2 and the recovery manifest remained. The manifest recorded exactly the
  contiguous prefix `[1, 2]`.
- With the same range and settings, **Resume** started immediately, completed frames 3–10, and
  retained a complete ten-frame processed sequence. Scene frame 7 remained restored.
- A further processing operation started immediately after Resume completed and could itself be
  cancelled before frame 1, confirming restart and cleanup after recovery.

## Remaining Blender UI limitation

Object Datamosh yields to Blender between raw frames and between processed frames. Processing stays
interactive at those boundaries. Raw rendering uses Blender 5.0's reliable synchronous
`EXEC_DEFAULT` frame-boundary fallback because a nested asynchronous render cancels its parent
modal operator in this release.

The foreground probe scheduled a 10 ms application heartbeat and observed **zero heartbeats while
an individual frame render was active** (450 heartbeats outside those intervals). Therefore an
individual raw frame can temporarily block the UI and delay Escape or Cancel feedback until Blender
returns from that frame. The sidebar redraws at the next verified safe boundary; the extension does
not claim within-frame render responsiveness or force-cancel through an undocumented API. See
[Blender 5.0 modal render investigation](blender-5-modal-render-investigation.md).

## Commands and results

Run from the repository root with
`BLENDER_BIN=/Applications/Blender.app/Contents/MacOS/Blender`:

| Command | Result |
|---|---|
| `uv run ty check` | Passed: `All checks passed!` |
| `uv run pytest -q` | Passed: 200 tests; 1 Blender-runtime test skipped outside Blender |
| `uv run ruff check .` | Passed: `All checks passed!` |
| `"$BLENDER_BIN" --background --factory-startup --python tests/blender_smoke_test.py` | Passed: `Object Datamosh Blender smoke test passed` |
| `"$BLENDER_BIN" --command extension validate src/object_datamosh` | Passed: manifest TOML parsed successfully |
| Foreground Blender 5.0.0 release probe, including a System Events Escape key | Passed: success, cancellation, Resume, restart, visible redraw, and cleanup assertions |
| `"$BLENDER_BIN" --command extension build --source-dir src/object_datamosh --output-dir dist` | Passed: `dist/object_datamosh-0.1.0.zip` |

The installation archive is `dist/object_datamosh-0.1.0.zip` (53,328 bytes), SHA-256
`9b36c10905ec76b9949e54655ad7c9f8a06e7776f4fc6f52119d5e8470c9b84d`.
The `dist/` directory is intentionally ignored by Git; the path above is relative to the repository
root where the release gate ran.

## Files changed

- `README.md` — links this current release record and distinguishes background coverage from the
  completed foreground verification.
- `docs/responsive-operations-release-verification.md` — records commands, packaging details,
  interactive observations, cancellation/recovery results, and the remaining UI limitation.
