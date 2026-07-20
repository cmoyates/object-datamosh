# Corrected Extreme workflow migration

This guide applies to processed sequences made before recovery manifest schema 5, especially a
schema-v2 run. Object Datamosh never guesses missing schema-v2 settings: the old fingerprint is
opaque and cannot establish orientation or the complete effective configuration.

## What a schema-v2 manifest establishes

A schema-v2 manifest with top-level `history_source: "TARGET_ONLY"` establishes that **Target
Only**, not Full Frame, was active for that run. It does not establish the omitted fallback, Trail,
motion, diffusion, refresh, reset, matte-provider, orientation, or provenance settings. Missing
readable settings must not be inferred from the image or fingerprint.

For compatibility, Target Only remains the global default and existing scene properties retain
their saved/default behavior. New schema-5 fields do not relax resume checks: orientation,
configuration, range, provider, reset policy, and contiguous completion metadata must all agree.
Processed output under an older or incompatible manifest is never silently accepted as history.

## Reprocess retained raw passes

The safe recovery path reuses raw beauty, Vector, and matte images and does not rerender the 3D
scene:

1. Preserve the old processed sequence and manifest as evidence, or choose a separate output root.
2. Confirm that every raw pass still follows the documented paths, frame range, dimensions, and
   matte contract.
3. In the sidebar, choose **Full Frame**, or apply **Extreme Full-Frame Feedback**, and verify the
   **Active** summary says `Full Frame / Trail`. A `Full-frame history is OFF` warning means Target
   Only is still selected.
4. Choose **Reprocess**, not **Resume**. Resume intentionally rejects schema 2 and any incompatible
   semantic settings.
5. If reusing the same output root, explicitly enable **Overwrite Processed Frames**. This permits
   replacement of the configured processed range only; raw overwrite remains independent and
   should stay off.
6. Run **Process Existing Passes**. Do not click **Render Raw Passes** or enable **Overwrite Raw
   Passes** unless rerendering is separately intended.
7. Inspect `processed/ODM_sequence_manifest.json` and
   `processed/ODM_processing_report.json` before relying on the sequence.

Object Datamosh never deletes old output. A separate root is the least destructive comparison path.
An interrupted reprocess trusts only the completed contiguous prefix recorded by its new manifest;
later stale processed files may remain but are pending. Resume is safe only with that new,
strictly compatible manifest.

## Reset and resume behavior

The configured first frame and every explicit reset frame seed clean current beauty and current
matte coverage. A background-only pre-roll therefore starts with clean background; when the target
enters later, Full Frame plus Same Screen Position can use prior background history. A reset on the
entrance frame instead seeds the clean entering object and weakens that effect.

If required processed history is absent or unreadable, **Missing History: Stop** preserves the
failure for diagnosis. **Missing History: Reset** rolls back to the recoverable boundary and seeds
clean state there; it does not fabricate history. Trail coverage is replayed deterministically from
the latest reset while retained processed color supplies recursive color history.

## Evidence to retain

Schema 5 records `image_orientation: "display_top_left_v1"` and readable `effective_settings`.
Confirm `history_source`, `mode`, `invalid_history_fallback`, `trail_motion_mix`, reset and
resolution policies, matte configuration, and version provenance. The report references that
manifest configuration and records fingerprint agreement, completion, warnings, and sampling and
change counters. A mismatch or missing report is not evidence of a successful corrected run.
