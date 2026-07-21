# CPU performance roadmap release validation (#79)

## Roadmap completion (#70)

Roadmap [#70](https://github.com/cmoyates/object-datamosh/issues/70) is complete: all nine child
issues were resolved and their pull requests merged to `main`. The durable outcome trail is:

| Child | Merged outcome | Durable evidence |
| --- | --- | --- |
| [#71](https://github.com/cmoyates/object-datamosh/issues/71) | Added the reproducible Extreme benchmark and bounded observational stage timings. | [PR #80](https://github.com/cmoyates/object-datamosh/pull/80), [`extreme-benchmark-baseline.json`](evidence/extreme-benchmark-baseline.json) |
| [#72](https://github.com/cmoyates/object-datamosh/issues/72) | Removed zero-refresh block scanning and vectorized nonzero refresh diagnostics. | [PR #82](https://github.com/cmoyates/object-datamosh/pull/82) |
| [#73](https://github.com/cmoyates/object-datamosh/issues/73) | Vectorized ZIP predictor reversal while retaining bit-identical decode results. | [PR #83](https://github.com/cmoyates/object-datamosh/pull/83), [`issue-73-exr-predictor.json`](evidence/issue-73-exr-predictor.json) |
| [#74](https://github.com/cmoyates/object-datamosh/issues/74) | Checkpointed diagnostics reports (295 to 31 writes in the 147-frame fixture) without changing per-frame recovery-manifest commits. | [PR #84](https://github.com/cmoyates/object-datamosh/pull/84), [`issue-74-diagnostics-checkpoint.json`](evidence/issue-74-diagnostics-checkpoint.json) |
| [#75](https://github.com/cmoyates/object-datamosh/issues/75) | Avoided redundant clean-history copies/sampling and made same-pixel fallback direct while preserving contaminated-history handling. | [PR #85](https://github.com/cmoyates/object-datamosh/pull/85), [`issue-75-full-frame-sampling.json`](evidence/issue-75-full-frame-sampling.json) |
| [#76](https://github.com/cmoyates/object-datamosh/issues/76) | Routed supported EXRs through the bundled decoder before Blender Images, retaining strict fallback and corruption behavior. | [PR #86](https://github.com/cmoyates/object-datamosh/pull/86), [`issue-76-custom-exr-routing.json`](evidence/issue-76-custom-exr-routing.json) |
| [#77](https://github.com/cmoyates/object-datamosh/issues/77) | Skipped motion/history work only for proven empty-effect frames. | [PR #87](https://github.com/cmoyates/object-datamosh/pull/87), [`issue-77-empty-effect-frames.json`](evidence/issue-77-empty-effect-frames.json) |
| [#78](https://github.com/cmoyates/object-datamosh/issues/78) | **Rejected** reusable bilinear plans: two-sample work was 4.11% slower and the modest 2.79% complete-feedback gain did not justify an 85,017,600-byte plan or 98.77 MiB peak-RSS growth. The prototype was reverted. | [PR #88](https://github.com/cmoyates/object-datamosh/pull/88), [`issue-78-bilinear-plans.json`](evidence/issue-78-bilinear-plans.json) |
| [#79](https://github.com/cmoyates/object-datamosh/issues/79) | Release-validated the integrated roadmap with same-harness workload, semantic, recovery, memory, and release-gate evidence. | [PR #89](https://github.com/cmoyates/object-datamosh/pull/89), [baseline](evidence/issue-79-workloads-baseline.json), [final](evidence/issue-79-workloads-final.json) |

The canonical cumulative measurements, 147-frame estimate, and machine-specific limitations are
recorded once in [Scope and result](#scope-and-result). Roadmap-wide semantic, recovery,
architecture, dependency, and scene-safety constraints are covered by the
[correctness and release gate](#correctness-and-release-gate). The remaining-stage analysis and
measured next direction are in [Bottleneck and recommendation](#bottleneck-and-recommendation).

## Scope and result

This is the cumulative release record for roadmap #70. The verification-gap rerun compares the
original PERF-1 revision (`0b19e06bb690227b3a3f0711dbb1acf1e91d5563`) with integrated production
code at `ce519792143f790731ef11e068fd1b1dab37a227` on the same machine. Both runs used the exact same
benchmark script bytes (SHA-256
`0dcedda7af5480a5138d439be0f2dc5447a2655df255c3e76eec82ec08cadd4f`), workload order, generated
1920×1080 float32 inputs, one warm-up per operation, one excluded sequence-path priming run, and
three measured samples. The JSON retains
each raw nanosecond sample as well as its count, median, range, and 147-frame extrapolation. This
supersedes the less-comparable workload coverage in the earlier release run.

For the canonical Extreme Full Frame + Trail non-reset frame, pure core fell from **2,070.206 ms**
to **293.871 ms** (**85.80% reduction / 7.04× speedup**) and measured complete-frame time fell from
**2,143.568 ms** to **791.130 ms** (**63.09% reduction / 2.71× speedup**). The latter gives a
median-only 147-frame processing estimate of **116.296 s (1.94 min)**, down from **315.104 s
(5.25 min)**. This is synthetic processing evidence on this machine, not a prediction for the
reporter's machine and not 3D render time.

Committed raw evidence:

- [`issue-79-workloads-baseline.json`](evidence/issue-79-workloads-baseline.json)
- [`issue-79-workloads-final.json`](evidence/issue-79-workloads-final.json)
- [`issue-79-perf1-baseline-rerun.json`](evidence/issue-79-perf1-baseline-rerun.json) (earlier run)
- [`issue-79-cumulative-release.json`](evidence/issue-79-cumulative-release.json) (earlier run)

## Exact method and environment

The benchmark creates deterministic EXRs in a temporary directory and uses production
`BlenderImageIO`, `process_frame_with_diagnostics`, and `process_sequence` paths. Each successful
workload has a standalone pure-core non-reset measurement, a complete two-frame sequence
measurement, an exact two-transition recursive output/state/coverage/diagnostics semantic
signature through frame 3, and every release stage from the non-reset frame. The canonical workload
also compares a three-frame uninterrupted run with an interruption after frame 1 followed by
manifest-backed Resume; all three resumed output hashes equal the uninterrupted hashes.
Sequence measurements use one configured warm-up followed by one excluded priming run of
its overwrite/recovery paths; each reported distribution then contains three samples. The two-frame
end-to-end range is reported as directly observed; the 147-frame values stored in the JSON normalize
its median by two.
For a long recursive sequence, the non-reset complete-frame estimate above is more representative
because only the first frame resets.

```bash
INTEGRATED_WORKTREE=$(git rev-parse --show-toplevel)
git worktree add --detach /tmp/odm-issue79-baseline-comparable 0b19e06
cp scripts/benchmark_release_workloads.py \
  /tmp/odm-issue79-baseline-comparable/scripts/benchmark_release_workloads.py
cd /tmp/odm-issue79-baseline-comparable
"$BLENDER_BIN" --background --factory-startup \
  --python scripts/benchmark_release_workloads.py -- \
  --warmups 1 --measured 3 --revision-label PERF-1 \
  --output "$INTEGRATED_WORKTREE/docs/evidence/issue-79-workloads-baseline.json"

cd "$INTEGRATED_WORKTREE"
"$BLENDER_BIN" --background --factory-startup \
  --python scripts/benchmark_release_workloads.py -- \
  --warmups 1 --measured 3 --revision-label final \
  --output docs/evidence/issue-79-workloads-final.json
```

| Metadata | Value |
| --- | --- |
| CPU | Apple M3 Max (`arm`) |
| OS | macOS 26.5.1 arm64 (Darwin 25.5.0) |
| Blender | 5.2.0 LTS, build `fbe6228777e7` |
| Python / NumPy | 3.13.13 / 2.3.4 |
| Fixture | 1920×1080 float32 RGBA, deterministic seed 71071 |
| Statistics | 1 warm-up per operation; 1 excluded sequence priming run; 3 samples; minimum / median / maximum |

## Workload evidence

All values below are **minimum / median / maximum milliseconds** from the same harness. The pure
core table times one non-reset frame. “Invalid resumed history” instead times its required,
end-to-end expected rejection: after a valid sequence is committed, processed frame 2 is replaced
with a 960×540 image while its matte and sequence remain 1920×1080. Resume must reject that history
before core processing or output writing, so fabricated stage values would be misleading.

| Workload (pure core, except expected rejection) | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Extreme Full Frame + Trail | 2068.377 / 2070.206 / 2135.142 | 288.677 / 293.871 / 302.048 |
| Extreme Hard | 2014.236 / 2023.286 / 2110.992 | 249.554 / 255.646 / 269.768 |
| Target Only compatibility | 6151.483 / 6180.027 / 6190.917 | 340.062 / 345.241 / 662.731 |
| background-only pre-roll | 2274.715 / 2361.050 / 2402.081 | 287.539 / 304.858 / 316.039 |
| nonzero refresh | 2141.145 / 2265.642 / 2428.635 | 285.951 / 290.411 / 303.113 |
| invalid resumed history (expected rejection) | 25.372 / 27.214 / 28.093 | 454.374 / 460.394 / 460.608 |

The invalid-resume rejection is slower in final code because the production bundled EXR reader
decodes and validates the malformed history before rejecting its resolution. It still rejects before
frame processing, as required; this unfavorable result is not hidden.

| Successful workload (complete two-frame end to end) | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Extreme Full Frame + Trail | 2223.814 / 2262.524 / 2294.566 | 1331.351 / 1332.614 / 1384.650 |
| Extreme Hard | 2254.794 / 2269.646 / 2276.748 | 1296.769 / 1300.961 / 1303.147 |
| Target Only compatibility | 7071.822 / 8056.682 / 12033.710 | 1287.471 / 1309.822 / 1346.377 |
| background-only pre-roll | 2464.920 / 2499.584 / 2621.653 | 1208.601 / 1242.784 / 1298.303 |
| nonzero refresh | 2267.220 / 2328.089 / 2353.873 | 1256.026 / 1289.628 / 1307.982 |

The exact workload definitions are committed in both JSON files. All `FeedbackSettings` fields,
target pixel counts, frame count, fixture metadata, workload order, environment, and harness hash
are equal across revisions. The committed two-transition semantic signatures prove exact equality
for processed RGBA, next history RGBA, next effect-coverage matte, frame number, and every diagnostic
counter for frames 2 and 3 of each successful workload (maximum numerical error **0**). Frame 3 consumes the
state returned by frame 2. The canonical committed recovery signature additionally proves that a
three-frame interrupted-and-resumed run exactly matches its uninterrupted counterpart.
Background-only pre-roll has zero target pixels on frame 1 and the normal target on frame 2;
nonzero refresh uses probability `0.25` and seed `73079`; Target Only uses compatibility defaults.

## Required release stages

Each table reports the successful workload's non-reset frame, again as **minimum / median / maximum
milliseconds**. “Total input read” is beauty + Vector + matte. “Complete frame” includes all listed
production stages and small orchestration overhead.

### Extreme Full Frame + Trail

| Stage | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Beauty read | 8.674 / 9.923 / 10.203 | 176.919 / 179.765 / 181.152 |
| Vector read | 3.705 / 3.722 / 3.739 | 138.340 / 138.607 / 140.050 |
| Matte read | 4.054 / 4.104 / 4.324 | 126.269 / 126.544 / 127.969 |
| Total input read | 16.451 / 17.952 / 18.046 | 441.795 / 446.073 / 447.746 |
| Core processing | 2067.469 / 2086.581 / 2097.223 | 298.519 / 301.337 / 309.815 |
| Processed EXR write | 37.113 / 38.003 / 50.909 | 44.379 / 45.510 / 80.148 |
| Recovery-manifest commit | 0.419 / 0.420 / 0.493 | 0.403 / 0.473 / 0.502 |
| Diagnostics-report commit | 0.387 / 0.439 / 0.458 | 0.383 / 0.433 / 0.440 |
| Complete frame | 2121.969 / 2143.568 / 2167.075 | 788.380 / 791.130 / 838.702 |

### Extreme Hard

| Stage | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Beauty read | 9.661 / 10.039 / 16.275 | 175.360 / 177.534 / 177.775 |
| Vector read | 4.122 / 4.248 / 4.552 | 134.776 / 137.882 / 138.686 |
| Matte read | 4.295 / 4.446 / 4.588 | 124.809 / 125.513 / 125.803 |
| Total input read | 18.355 / 18.456 / 25.415 | 438.064 / 438.854 / 441.219 |
| Core processing | 2036.841 / 2061.740 / 2070.361 | 256.760 / 265.648 / 268.844 |
| Processed EXR write | 35.862 / 43.822 / 60.471 | 37.114 / 52.078 / 55.783 |
| Recovery-manifest commit | 0.385 / 0.388 / 0.403 | 0.399 / 0.413 / 0.449 |
| Diagnostics-report commit | 0.345 / 0.365 / 0.523 | 0.364 / 0.392 / 0.422 |
| Complete frame | 2098.937 / 2133.647 / 2141.398 | 741.712 / 750.901 / 764.432 |

### Target Only compatibility

| Stage | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Beauty read | 8.817 / 9.844 / 23.745 | 168.029 / 169.373 / 179.314 |
| Vector read | 3.834 / 4.587 / 7.634 | 132.236 / 132.953 / 136.296 |
| Matte read | 4.475 / 5.085 / 7.994 | 117.213 / 117.437 / 127.335 |
| Total input read | 17.127 / 19.516 / 39.374 | 419.763 / 428.763 / 431.660 |
| Core processing | 6790.016 / 7617.598 / 11494.767 | 328.870 / 335.974 / 338.010 |
| Processed EXR write | 104.635 / 114.910 / 239.028 | 28.328 / 31.667 / 38.166 |
| Recovery-manifest commit | 1.464 / 1.798 / 5.900 | 0.423 / 0.426 / 0.509 |
| Diagnostics-report commit | 0.855 / 0.860 / 1.225 | 0.367 / 0.384 / 0.388 |
| Complete frame | 6928.903 / 7879.115 / 11641.720 | 785.052 / 799.334 / 799.577 |

### background-only pre-roll

| Stage | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Beauty read | 9.887 / 10.467 / 11.296 | 173.448 / 173.945 / 174.018 |
| Vector read | 4.108 / 4.367 / 4.427 | 133.395 / 134.841 / 134.916 |
| Matte read | 4.779 / 4.981 / 5.103 | 114.904 / 120.041 / 124.149 |
| Total input read | 18.976 / 19.673 / 20.766 | 422.317 / 428.405 / 432.936 |
| Core processing | 2211.457 / 2245.295 / 2342.849 | 282.241 / 298.055 / 301.573 |
| Processed EXR write | 66.754 / 75.949 / 101.373 | 29.807 / 33.429 / 49.880 |
| Recovery-manifest commit | 0.478 / 0.571 / 0.598 | 0.417 / 0.431 / 0.435 |
| Diagnostics-report commit | 0.416 / 0.439 / 0.580 | 0.332 / 0.367 / 0.392 |
| Complete frame | 2298.871 / 2343.124 / 2464.485 | 755.300 / 761.645 / 764.318 |

### nonzero refresh

| Stage | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Beauty read | 8.034 / 9.267 / 9.897 | 166.656 / 168.395 / 172.850 |
| Vector read | 3.546 / 3.892 / 4.277 | 132.157 / 133.210 / 137.018 |
| Matte read | 4.246 / 4.375 / 4.551 | 118.284 / 118.577 / 122.679 |
| Total input read | 15.955 / 17.405 / 18.725 | 418.444 / 418.837 / 432.546 |
| Core processing | 2093.361 / 2094.320 / 2142.591 | 292.654 / 292.901 / 309.256 |
| Processed EXR write | 53.068 / 59.613 / 76.500 | 38.914 / 50.112 / 57.998 |
| Recovery-manifest commit | 0.388 / 0.416 / 1.498 | 0.396 / 0.410 / 0.445 |
| Diagnostics-report commit | 0.321 / 0.343 / 0.387 | 0.353 / 0.382 / 0.502 |
| Complete frame | 2165.673 / 2187.730 / 2221.720 | 751.279 / 776.586 / 786.526 |

## Memory evidence and limits

Both committed evidence files contain the same representative live-array definition: beauty RGBA,
Vector RGBA, matte, history RGBA, and history matte, all 1920×1080 float32. That footprint is
**116,121,600 bytes (110.74 MiB)** at both revisions. In isolated Blender benchmark processes,
`resource.getrusage(RUSAGE_SELF).ru_maxrss` recorded a process peak of **1,168,408,576 bytes
(1,114.28 MiB)** at PERF-1 and **1,228,308,480 bytes (1,171.41 MiB)** final:
**+5.13% (+57.12 MiB)**.

This is auditable and directly comparable because the harness hash, machine, operation order, fixture,
and process scope match. It is still a process-wide high-water mark, not a per-stage allocation
profile: Blender, generated EXRs, decoder buffers, Python/NumPy allocators, and prior workloads in
the fixed order are included. It cannot identify transient ownership or extrapolate peak memory to
a 147-frame run; recursive processing retains only the current history, but allocator behavior is
implementation-dependent.

## Correctness and release gate

The same-harness cumulative semantic signatures and deterministic roadmap fixtures remain
bit-for-bit equal (maximum numerical error **0**). Coverage includes Full Frame + Trail, Extreme
Hard, Target Only,
background-only pre-roll, nonzero deterministic refresh, two recursive transitions per successful
workload, manifest-backed interrupted/resumed equivalence, and malformed resumed history,
partial edge blocks, output/state/coverage and diagnostics equivalence, Blender orientation,
temporary image/data cleanup, and every roadmap benchmark contract. The actual Blender smoke
fixture and calibration scene are separate from this synthetic benchmark.

A fresh integrated gate was run from expected review SHA
`ce519792143f790731ef11e068fd1b1dab37a227` after regenerating the evidence above. Results are
committed here rather than relying only on the PR description:

| Command | Result |
| --- | --- |
| `uv run ty check` | Pass |
| `uv run pytest -q` | Pass: 460 passed, 1 Blender-only test skipped |
| `uv run ruff check .` | Pass |
| `uv run ruff format --check .` | Pass: 77 files formatted |
| `"$BLENDER_BIN" --background --factory-startup --python tests/blender_smoke_test.py` | Pass on Blender 5.2.0 LTS; exercised the actual 65×37 Extreme fixture, recursive four-frame output, orientation, resume/recovery, invalid-history, and temporary-data cleanup checks |
| `"$BLENDER_BIN" --background --factory-startup --python tests/create_calibration_scene.py` | Pass |
| `"$BLENDER_BIN" --command extension validate src/object_datamosh` | Pass |
| `mkdir -p dist && "$BLENDER_BIN" --command extension build --source-dir src/object_datamosh --output-dir dist` | Pass: `dist/object_datamosh-0.3.0.zip`, 69,323 bytes; ZIP integrity passed |

The smoke fixture is a correctness and Blender-integration gate, not a processing benchmark row:
its 3D render, compositor setup, tiny 65×37 arrays, cancellation paths, and processing are deliberately
mixed. Treating its wall time as comparable to the isolated 1920×1080 stage workloads would blur
the processing-versus-render distinction the issue requires.

## Bottleneck and recommendation

For final Extreme Full Frame + Trail medians, input reads consume **446.073 ms (56.4%)**, core
processing **301.337 ms (38.1%)**, and output writing **45.510 ms (5.8%)** of the **791.130 ms**
complete frame. Manifest and diagnostics commits together are below 1 ms. The read regression is
real: PERF-1 used Blender loading for simple generated EXRs, while final production routing uses the
bundled decoder and transfer path. The exact same generated fixtures and production calls are used
at both revisions, so this is a cumulative release comparison rather than an isolated storage test.

**Recommended next direction: more CPU work focused narrowly on bundled EXR decode and
copy/transfer.** Output-writer or GPU work is not supported as the next roadmap by these data.
Rendering the 3D scene happens before these passes exist and is excluded. Remaining risks are that
the synthetic fixture does not model the reporter's storage, scene, compositor, or render engine;
RSS is not an allocation profile; and no interactive artistic/viewport inspection was performed.
