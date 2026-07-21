# CPU performance roadmap release validation (#79)

## Scope and result

This is the cumulative release record for roadmap #70. It compares the original PERF-1 source
revision (`0b19e06`) with the integrated issue #79 branch on the same machine and deterministic
1920×1080 float32 Extreme Full Frame + Trail fixture. The integrated gate passed. Deterministic
core fixtures remain bit-for-bit equal (maximum error **0**) across the roadmap evidence and full
regression suite; no GPU code, frame parallelism, compiled dependency, or artistic-output change
was introduced.

The final non-reset complete-frame median is **757.942 ms**, down from **2,128.738 ms**: a
**64.39% reduction / 2.81× speedup**. The median-only 147-frame processing extrapolation is
**111.417 s (1.86 min)**, down from **312.924 s (5.22 min)**. This is synthetic processing evidence
on the measured machine, not a prediction for the reporter's machine and not 3D render time.

Raw evidence:

- [`issue-79-perf1-baseline-rerun.json`](evidence/issue-79-perf1-baseline-rerun.json)
- [`issue-79-cumulative-release.json`](evidence/issue-79-cumulative-release.json)

## Method and environment

Both revisions used one warm-up and three measured benchmark iterations, `perf_counter_ns`, the
same deterministic seed (71071), generated temporary EXRs, and
`extreme_full_frame_feedback_settings()`. The final release-stage distributions contain the two
non-reset frames from each of three report runs (six samples); diagnostics-report commits occur only
at the terminal checkpoint and therefore have three samples. The old report retained only its final
run, so old per-frame stage distributions contain two non-reset samples. Standalone core, EXR read,
write, and complete-sequence measurements have three samples per revision. Values below are
minimum / median / maximum milliseconds.

Exact commands:

```bash
git worktree add --detach /tmp/odm-issue79-baseline 0b19e06
cd /tmp/odm-issue79-baseline
/usr/bin/time -l "$BLENDER_BIN" --background --factory-startup \
  --python scripts/benchmark_extreme.py -- \
  --warmups 1 --measured 3 --output /tmp/issue79-baseline-rerun.json

cd <integrated-worktree>
/usr/bin/time -l "$BLENDER_BIN" --background --factory-startup \
  --python scripts/benchmark_extreme.py -- \
  --warmups 1 --measured 3 --output docs/evidence/issue-79-cumulative-release.json
```

| Metadata | Value |
| --- | --- |
| CPU | Apple M3 Max (`arm`) |
| OS | macOS 26.5.1 arm64 (Darwin 25.5.0) |
| Blender | 5.2.0 LTS, build `fbe6228777e7` |
| Python / NumPy | 3.13.13 / 2.3.4 |
| Fixture | 1920×1080 float32 RGBA, 3 sequential frames, Extreme Full Frame + Trail |
| Statistics | 1 warm-up; 3 measured runs; median plus minimum/maximum |

## Same-machine before and after

| Stage | PERF-1 min / median / max (ms) | Final min / median / max (ms) | Median change | Speedup |
| --- | ---: | ---: | ---: | ---: |
| Pure core (standalone) | 2018.933 / 2029.789 / 2030.885 | 289.898 / 295.410 / 299.506 | 85.45% faster | 6.87× |
| Beauty read | 8.325 / 8.906 / 9.488 | 168.526 / 170.892 / 174.456 | 1818.75% slower | 0.05× |
| Vector read | 3.564 / 3.698 / 3.833 | 132.091 / 133.876 / 136.716 | 3519.87% slower | 0.03× |
| Matte read | 4.310 / 4.548 / 4.785 | 117.031 / 123.199 / 124.367 | 2609.01% slower | 0.04× |
| Total input read | 16.199 / 17.153 / 18.106 | 420.432 / 427.202 / 434.624 | 2390.60% slower | 0.04× |
| Core processing (sequence) | 2074.724 / 2076.227 / 2077.729 | 282.000 / 294.053 / 301.744 | 85.84% faster | 7.06× |
| Processed EXR write | 32.677 / 34.445 / 36.213 | 31.482 / 33.603 / 51.270 | 2.44% faster | 1.03× |
| Recovery-manifest commit | 0.401 / 0.455 / 0.509 | 0.403 / 0.434 / 0.490 | 4.71% faster | 1.05× |
| Diagnostics-report commit | 0.372 / 0.380 / 0.389 | 0.383 / 0.386 / 0.403 | 1.57% slower | 0.98× |
| Complete non-reset frame | 2126.476 / 2128.738 / 2131.000 | 744.313 / 757.942 / 770.857 | 64.39% faster | 2.81× |

The complete three-frame sequential benchmark (including the reset frame) fell from a
4,307.924 ms median to 2,040.315 ms, a **52.64% reduction / 2.11× speedup**. The standalone write
measurement was 64.549 ms before and 37.448 ms final; the per-frame table above is preferred for the
integrated workload.

The unfavorable read result is intentional evidence, not hidden noise. PERF-1 used Blender image
loading for simple generated EXRs. The final integrated route uses the bundled decoder first and the
release fixture also exercises compositor-shaped multilayer ZIP files. That routing avoids temporary
Blender image data-blocks and preserves orientation/cleanup behavior, but ZIP decode and array
transfer now dominate elapsed processing. The comparison therefore measures cumulative production
routing rather than an isolated storage-device read.

## Memory

The deterministic beauty, Vector, matte, history, and history-matte arrays represent
**116,121,600 bytes (110.74 MiB)** at both revisions. `/usr/bin/time -l` measured process peak RSS at
**1,059,307,520 bytes (1010.23 MiB)** for PERF-1 and **1,200,308,224 bytes (1144.70 MiB)** final,
an increase of **13.31% (134.47 MiB)**. The final JSON records both the representative live-array
footprint and process peak. RSS is a process-wide peak after every workload and is not an allocation
profile; Blender, decoder buffers, and benchmark fixture construction are included.

## Correctness and release gate

The pure test suite passed **459 tests with 1 Blender-runtime skip**. It covers Full Frame + Trail,
Extreme Hard, Target Only compatibility, background-only pre-roll, nonzero deterministic refresh,
resume/recovery, malformed and invalid resumed history, partial edge blocks, output/state/coverage
and diagnostics equivalence, atomic recovery manifests, diagnostics checkpointing, and every
roadmap benchmark contract. The actual Blender smoke fixture passed and exercised Extreme,
Target Only / Hard Localized, background operation, orientation-sensitive EXR round trips,
cancellation/recovery, idempotent setup restoration, and temporary image/data cleanup.

Commands and results:

```text
uv run ty check                                                PASS
uv run pytest -q                                               PASS (459 passed, 1 skipped)
uv run ruff check .                                            PASS
uv run ruff format --check .                                   PASS
"$BLENDER_BIN" --background --factory-startup \
  --python tests/blender_smoke_test.py                          PASS
"$BLENDER_BIN" --background --factory-startup \
  --python tests/create_calibration_scene.py                    PASS
"$BLENDER_BIN" --command extension validate src/object_datamosh PASS
mkdir -p dist
"$BLENDER_BIN" --command extension build \
  --source-dir src/object_datamosh --output-dir dist            PASS
```

The built installation archive is `dist/object_datamosh-0.3.0.zip` (69,323 bytes). Large EXR
fixtures remain temporary. The benchmark contracts and pinned evidence for refresh diagnostics,
ZIP predictor reversal, diagnostics checkpointing, Full Frame sampling, direct EXR routing,
empty-effect frames, and the rejected bilinear-plan prototype are all covered by the complete test
run; historical revision guards remain unchanged.

## Bottleneck and recommendation

At final medians, input reads consume **427.202 ms (56.4%)**, core processing **294.053 ms
(38.8%)**, and output writing **33.603 ms (4.4%)** of a 757.942 ms non-reset frame. Manifest and
report commits together are below 1 ms. Rendering the 3D scene happens before these raw passes exist
and is excluded; storage, scene, compositor, and render-engine costs can differ substantially on
another machine.

**Recommended next direction: more CPU work focused narrowly on the bundled ZIP EXR decode and
copy/transfer path.** The evidence does not support output-writer work or optional GPU compute as
the next roadmap: writes are only 4.4%, while read/decode/transfer is the largest measured stage.
Any follow-up must first isolate predictor, decompression, channel assembly, and copies without
changing fallback or corruption semantics. This release ticket does not implement that roadmap.

Remaining risks: the synthetic three-frame fixture cannot model the reporter's storage or 3D render
time; RSS is a process peak rather than per-stage memory; and no interactive viewport/artistic
inspection was performed. Automated semantic fixtures, Blender orientation/cleanup checks,
calibration generation, validation, and packaging did pass.
