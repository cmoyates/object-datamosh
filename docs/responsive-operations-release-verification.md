# Responsive operations release verification

Date: 2026-07-19

Release: Object Datamosh 0.1.0

Platform: Blender 5.0.0 (`a37564c4df7a`), macOS, Apple Silicon

Tested extension source tree: `fdf85c1a6ea159986a0e925759dedc5830b6616c` (the
`src/object_datamosh` tree at base commit `77a14071b418950db1e06536889457d954395153`;
this issue changes release documentation and its release probe only)

Foreground probe and release-gate revision: `e6628a8a595aaa53416fc205c15f82836c3819ae`

Issue: [#26 — Verify and release responsive operations](https://github.com/cmoyates/object-datamosh/issues/26)

## Result

The responsive modal operations passed the pure-Python, static-analysis, Blender background,
extension-validation, packaging, and foreground Blender checks listed below. No implementation
defect was found, so this release-verification change is documentation-only.

The foreground checks used an actual Blender window and window-manager event loop, not
`--background`. The tracked `scripts/issue26_foreground_probe.py` fixture rendered a 32×24 Cycles
scene at one sample so ten complete frames could be checked quickly. It observed scene-owned
runtime values by wrapping the registered production `ODM_PT_sidebar.draw` method at every redraw.
The probe opens the UI region and selects its real **Object Datamosh** panel category before running.
The tracked shell runner sent real macOS Escape key events to the foreground Blender application
through System Events. The same macOS UI process selected the production tab and delivered real
left-mouse clicks to its **Cancel** control during raw and processing runs; the runtime response
identifies the successful button coordinate retained in the receipt.
The retained
[`docs/evidence/issue-26-foreground-result.json`](evidence/issue-26-foreground-result.json)
atomically bundles the assertion summary and complete JSONL event trace, including the terminal
`probe_complete` event; the recorded SHA-256 verifies the embedded trace.

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

- A real left-mouse event clicked the production sidebar **Cancel** button during raw rendering. The
  runtime displayed `Cancel requested; waiting for a safe boundary...`, remained active while
  cancellation was pending, then ended inactive at `CANCELLED`. The receipt records the exact
  contiguous completed prefix and verifies that no beauty, Vector, or matte file for the next frame
  was created.
- A real **Escape** key event was sent by macOS System Events during an active 100-frame raw render.
  Retained monotonic markers prove that both the send invocation and its completion occurred inside
  same raw render interval. Blender queued the event while blocked, completed a contiguous two-frame
  prefix, and then moved directly to the terminal state at the next safe boundary; no frame 3 pass
  was created. Unlike the button path, this active-render path did not visibly dwell in the pending
  state. That Blender 5.0 limitation is documented below rather than reported as responsiveness.
- Both runs restored scene frame 7. The owned render-complete/render-cancel handler counts returned
  to their baseline, and another operation started immediately, demonstrating that the modal timer,
  handlers, and operation lock no longer owned an active run.

### Cancellation and Resume during existing-pass processing

Two separate runs covered both user inputs:

- **Process Existing Passes** was cancelled by a real left-mouse event on the production sidebar
  button after at least two complete frames. The pending status appeared, no output after the exact
  receipt-recorded prefix started, and the run ended inactive at `CANCELLED`. Scene frame 7 was
  unchanged, the active-controller entry was
  cleared, and no stale modal event changed the terminal state. Blender exposes no public event-
  timer enumeration, so timer cleanup was verified indirectly by the cleared controller/lock and
  immediate successful restart; the background smoke fixture separately observes `timer_remove`.
- Processed frames 1–2 and the recovery manifest remained. The manifest recorded exactly the
  contiguous prefix `[1, 2]`.
- With the same range and settings, **Resume** started immediately, completed frames 3–10, and
  retained a complete ten-frame processed sequence. Scene frame 7 remained restored.
- In a separate run, a real **Escape** key event produced visible pending and terminal states. The
  manifest and files retained the same exact contiguous prefix recorded in the receipt, with no
  next output. Scene frame 7 was unchanged, runtime and controller lock were inactive, **Resume**
  completed the remaining frames, and another processing operation started immediately.
- A further processing operation started immediately after Resume completed and could itself be
  cancelled before frame 1, confirming restart and cleanup after recovery.

## Repeatable foreground checklist

Run the tracked foreground probe on macOS (with Accessibility permission for System Events):

```bash
BLENDER_BIN=/Applications/Blender.app/Contents/MacOS/Blender \
  scripts/run_issue26_foreground_probe.sh --update-evidence
```

The runner starts non-background Blender with factory settings, waits for explicit raw-active and
processing Escape checkpoints, sends each real key event, and has bounded waits. A stable per-user
persistent kernel lock serializes this runner with release-receipt promotion, and System Events
confirms the launched Blender PID is frontmost
immediately before each key event. The run fails unless its Blender-side state checkpoints and
result JSON say `"success": true`. By default it leaves a
unique run directory outside the checkout;
`--update-evidence` explicitly promotes a successful result atomically to the tracked receipt. Its
assertions implement this checklist:

1. Load the clean extension source tree identified above, start Blender 5.0.0 with factory settings,
   and configure the temporary 32×24 Cycles scene with one sample and frames 1–10. Set the scene
   frame to 7, configure Object Index, and keep the Object Datamosh sidebar visible.
2. Run **Render and Process**. At every redraw, record phase, current frame, phase work, overall
   work, and progress. Require rendering boundaries 0–9, processing boundaries 10–19, terminal
   20/20, all four ten-file sequences, restored frame 7, and an immediately startable second run.
3. Click the production sidebar Cancel button during one raw run and require visible pending then
   terminal states plus an exact bounded prefix. In a separate 100-frame raw run, send a real Escape
   key during an instrumented
   active render interval. Require a contiguous retained raw prefix, no next-frame pass, restored
   frame 7, inactive runtime, a cleared active-controller lock, unchanged render-handler counts,
   and immediate restart. Record whether the active-render path visibly dwells in pending state.
4. Copy the successful raw inputs to fresh output roots. Cancel **Process Existing Passes** with
   the button after frame 2 and with Escape in a separate run, then inspect each
   `ODM_sequence_manifest.json`. Require the exact retained prefix, restored frame 7, inactive
   runtime, a cleared controller lock, and no later output. Select **Resume**, require completion of
   each sequence, then start and cancel one further operation immediately.
5. To check the remaining UI limitation, register a 10 ms `bpy.app.timers` heartbeat and
   `render_pre`/`render_complete` markers before step 2. Compare heartbeat timestamps with each raw
   render interval; do not claim within-frame responsiveness if no heartbeat occurs there.

Record the Blender build hash, tested Git commit or extension-source tree, command results, archive
path/size/SHA-256, observed state transitions, handler baselines, and any deviation from these
expected results in the release report.

## Remaining Blender UI limitation

Object Datamosh yields to Blender between raw frames and between processed frames. Processing stays
interactive at those boundaries. Raw rendering uses Blender 5.0's reliable synchronous
`EXEC_DEFAULT` frame-boundary fallback because a nested asynchronous render cancels its parent
modal operator in this release.

The latest foreground probe scheduled a 10 ms application heartbeat and observed **zero heartbeats
while an individual frame render was active** (599 heartbeats outside those intervals). Therefore an
individual raw frame can temporarily block the UI and delay Escape or Cancel feedback until Blender
returns from that frame. The active-render Escape observation also moved directly to terminal
**Cancelled** without a visibly persistent pending state. The sidebar redraws at the next verified
safe boundary; the extension does not claim within-frame render responsiveness or force-cancel
through an undocumented API. See
[Blender 5.0 modal render investigation](blender-5-modal-render-investigation.md).

## Commands and results

The tracked `scripts/issue26_release_gates.py` executes the non-foreground gates, captures each exit
code, output digest/tail, Git/source identity, and ZIP metadata, and writes the successful
machine-readable aggregate at `docs/evidence/issue-26-release-gates.json` only with explicit
`--update-evidence`. It validates the foreground result, trace digest, tested revision, source tree,
and probe/runner hashes, then executes each gate from an isolated detached worktree. Each command
atomically writes a fixed-name receipt with exit status, output digest, and bounded 32 KiB head plus
32 KiB tail with explicit truncation metadata; launch, command, and tracked-mutation failures are
receipted before stopping. The package builds in a
unique temporary directory and is published to `dist/` only if it does not conflict with an existing
archive. Receipt-publication commits change evidence/documentation only; the recorded revisions
identify the executable trees that were actually run.

Run from the repository root with
`BLENDER_BIN=/Applications/Blender.app/Contents/MacOS/Blender`:

| Command | Result |
|---|---|
| `uv run ty check` | Passed: `All checks passed!` |
| `uv run pytest -q` | Passed: 205 tests; 1 Blender-runtime test skipped outside Blender |
| `uv run ruff check .` | Passed: `All checks passed!` |
| `"$BLENDER_BIN" --background --factory-startup --python tests/blender_smoke_test.py` | Passed: `Object Datamosh Blender smoke test passed` |
| `"$BLENDER_BIN" --command extension validate src/object_datamosh` | Passed: manifest TOML parsed successfully |
| `scripts/run_issue26_foreground_probe.sh --update-evidence` | Passed in foreground Blender 5.0.0: active-render/production Cancel-button/Escape cancellation, Resume, restart, production-panel redraw, and cleanup assertions; retained JSON reports `success: true` and binds the Blender build, Git HEAD, source tree, probe, runner, and event-log digest |
| `"$BLENDER_BIN" --command extension build --source-dir src/object_datamosh --output-dir <unique-temp>/build` | Passed; the newly built archive was published without replacing the existing `dist/` artifact |

The installation archive is `dist/object_datamosh-0.1.0-97ace3d03496.zip` (53,328 bytes), SHA-256
`97ace3d03496e4af90ac5f38d13c4e24ffaa077dd350666f25cc9ae34a990f06`.
The `dist/` directory is intentionally ignored by Git; the path above is relative to the repository
root where the release gate ran.

## Files changed

- `README.md` — links this current release record, lists every repository gate, and distinguishes
  background coverage from the completed foreground verification.
- `docs/responsive-operations-release-verification.md` — records commands, packaging details,
  interactive observations, cancellation/recovery results, and the remaining UI limitation.
- `pyproject.toml` — includes authored foreground-probe Python in the `ty` boundary.
- `scripts/issue26_foreground_probe.py` — runs the foreground Blender assertions and writes evidence.
- `scripts/run_issue26_foreground_probe.sh` — launches the probe and sends real raw/processing
  Escape events through macOS System Events.
- `scripts/issue26_release_gates.py` — executes and receipts static, pure-Python, Blender background,
  validation, and package-build gates.
- `docs/evidence/issue-26-foreground-result.json` — atomically retains the successful foreground
  assertions and exact monotonic event trace for the tested source tree and probe revision.
- `docs/evidence/issue-26-gate-<name>.json` — atomically retains each gate's tested revision, exit
  status, bounded output, digest, byte counts, and truncation state, including failures.
- `docs/evidence/issue-26-release-gates.json` — atomically references the successful per-gate
  receipts and retains foreground-receipt identity plus ZIP metadata.
- `tests/test_issue26_release_gates.py` — verifies identity comparison and detects a real mid-run
  project-file edit in a temporary Git repository.
