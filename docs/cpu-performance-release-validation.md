# CPU performance roadmap release validation (#79)

## Scope and result

This is the cumulative release record for roadmap #70. The verification-gap rerun compares the
original PERF-1 revision (`0b19e06bb690227b3a3f0711dbb1acf1e91d5563`) with integrated production
code at `5ca5134a8a82b1cf413dae7a3c9b6224e281769a` on the same machine. Both runs used the exact same
benchmark script bytes (SHA-256
`862577a7a12ef31d3f3caf045902d0c7bbff4f9e6b2c5bf7ac2dabedd1761576`), workload order, generated
1920×1080 float32 inputs, one warm-up per operation, and three measured samples. The JSON retains
each raw nanosecond sample as well as its count, median, range, and 147-frame extrapolation. This
supersedes the less-comparable workload coverage in the earlier release run.

For the canonical Extreme Full Frame + Trail non-reset frame, pure core fell from **1,988.777 ms**
to **304.567 ms** (**84.69% reduction / 6.53× speedup**) and measured complete-frame time fell from
**2,065.286 ms** to **760.567 ms** (**63.17% reduction / 2.72× speedup**). The latter gives a
median-only 147-frame processing estimate of **111.803 s (1.86 min)**, down from **303.597 s
(5.06 min)**. This is synthetic processing evidence on this machine, not a prediction for the
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
measurement, and every release stage from the non-reset frame. The two-frame end-to-end range is
reported as directly observed; the 147-frame values stored in the JSON normalize its median by two.
For a long recursive sequence, the non-reset complete-frame estimate above is more representative
because only the first frame resets.

```bash
git worktree add --detach /tmp/odm-issue79-baseline-comparable 0b19e06
cp scripts/benchmark_release_workloads.py \
  /tmp/odm-issue79-baseline-comparable/scripts/benchmark_release_workloads.py
cd /tmp/odm-issue79-baseline-comparable
"$BLENDER_BIN" --background --factory-startup \
  --python scripts/benchmark_release_workloads.py -- \
  --warmups 1 --measured 3 --revision-label PERF-1 \
  --output /tmp/issue-79-workloads-baseline.json

cd <integrated-worktree>
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
| Statistics | 1 warm-up per operation; 3 samples; minimum / median / maximum |

## Workload evidence

All values below are **minimum / median / maximum milliseconds** from the same harness. The pure
core table times one non-reset frame. “Invalid resumed history” instead times its required,
end-to-end expected rejection: after a valid sequence is committed, processed frame 2 is replaced
with a 960×540 image while its matte and sequence remain 1920×1080. Resume must reject that history
before core processing or output writing, so fabricated stage values would be misleading.

| Workload (pure core, except expected rejection) | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Extreme Full Frame + Trail | 1975.505 / 1988.777 / 2023.444 | 284.870 / 304.567 / 346.234 |
| Extreme Hard | 1890.542 / 1891.655 / 1920.536 | 235.134 / 236.410 / 288.330 |
| Target Only compatibility | 5943.924 / 5959.974 / 5961.721 | 323.308 / 413.902 / 440.594 |
| background-only pre-roll | 1982.878 / 1986.179 / 2005.183 | 280.861 / 281.119 / 281.361 |
| nonzero refresh | 1966.880 / 2029.364 / 2030.989 | 282.671 / 285.550 / 289.188 |
| invalid resumed history (expected rejection) | 21.363 / 21.824 / 24.300 | 437.773 / 442.091 / 443.050 |

The invalid-resume rejection is slower in final code because the production bundled EXR reader
decodes and validates the malformed history before rejecting its resolution. It still rejects before
frame processing, as required; this unfavorable result is not hidden.

| Successful workload (complete two-frame end to end) | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Extreme Full Frame + Trail | 2148.538 / 2181.231 / 2185.372 | 1238.480 / 1259.821 / 1315.469 |
| Extreme Hard | 2068.264 / 2093.077 / 2167.729 | 1183.394 / 1190.962 / 1218.057 |
| Target Only compatibility | 6080.004 / 6130.840 / 6171.782 | 1265.765 / 1315.879 / 1342.449 |
| background-only pre-roll | 2172.970 / 2188.646 / 2202.867 | 1209.077 / 1241.567 / 1346.580 |
| nonzero refresh | 2160.992 / 2188.518 / 2220.146 | 1228.939 / 1247.323 / 1251.490 |

The exact workload definitions are committed in both JSON files. All `FeedbackSettings` fields,
target pixel counts, frame count, fixture metadata, workload order, environment, and harness hash
are equal across revisions.
Background-only pre-roll has zero target pixels on frame 1 and the normal target on frame 2;
nonzero refresh uses probability `0.25` and seed `73079`; Target Only uses compatibility defaults.

## Required release stages

Each table reports the successful workload's non-reset frame, again as **minimum / median / maximum
milliseconds**. “Total input read” is beauty + Vector + matte. “Complete frame” includes all listed
production stages and small orchestration overhead.

### Extreme Full Frame + Trail

| Stage | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Beauty read | 7.684 / 7.746 / 7.967 | 166.892 / 167.152 / 167.348 |
| Vector read | 3.140 / 3.237 / 3.392 | 130.022 / 130.756 / 132.876 |
| Matte read | 3.733 / 3.898 / 4.013 | 119.392 / 119.648 / 122.367 |
| Total input read | 14.716 / 14.837 / 15.257 | 417.018 / 417.300 / 422.135 |
| Core processing | 1994.454 / 2008.522 / 2023.320 | 289.856 / 293.356 / 307.589 |
| Processed EXR write | 31.071 / 32.547 / 55.161 | 31.266 / 34.712 / 87.446 |
| Recovery-manifest commit | 0.380 / 0.392 / 0.443 | 0.388 / 0.419 / 0.478 |
| Diagnostics-report commit | 0.371 / 0.373 / 0.379 | 0.360 / 0.370 / 0.403 |
| Complete frame | 2055.665 / 2065.286 / 2071.467 | 744.080 / 760.567 / 798.684 |

### Extreme Hard

| Stage | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Beauty read | 7.593 / 7.782 / 7.966 | 167.401 / 168.364 / 171.808 |
| Vector read | 3.473 / 3.523 / 3.566 | 129.630 / 130.347 / 130.469 |
| Matte read | 3.752 / 3.776 / 4.145 | 115.498 / 115.557 / 118.827 |
| Total input read | 14.818 / 15.081 / 15.677 | 414.331 / 415.858 / 417.711 |
| Core processing | 1888.439 / 1912.262 / 1976.525 | 236.200 / 247.949 / 268.954 |
| Processed EXR write | 49.953 / 53.013 / 54.669 | 29.935 / 30.269 / 31.793 |
| Recovery-manifest commit | 0.383 / 0.411 / 0.452 | 0.397 / 0.441 / 0.503 |
| Diagnostics-report commit | 0.369 / 0.380 / 0.384 | 0.378 / 0.386 / 0.395 |
| Complete frame | 1954.110 / 1981.174 / 2047.734 | 684.822 / 693.115 / 717.804 |

### Target Only compatibility

| Stage | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Beauty read | 9.567 / 10.514 / 10.685 | 167.494 / 170.401 / 176.733 |
| Vector read | 3.499 / 3.506 / 3.868 | 129.997 / 132.846 / 133.300 |
| Matte read | 3.893 / 3.897 / 3.932 | 121.422 / 121.786 / 124.322 |
| Total input read | 16.958 / 17.916 / 18.485 | 422.216 / 424.721 / 431.365 |
| Core processing | 5916.240 / 5960.188 / 6005.197 | 319.048 / 355.591 / 357.863 |
| Processed EXR write | 32.927 / 33.110 / 50.364 | 28.482 / 29.082 / 36.002 |
| Recovery-manifest commit | 0.419 / 0.451 / 0.570 | 0.377 / 0.399 / 0.400 |
| Diagnostics-report commit | 0.392 / 0.404 / 0.455 | 0.370 / 0.374 / 0.386 |
| Complete frame | 5985.440 / 6011.405 / 6057.488 | 770.593 / 817.143 / 819.183 |

### background-only pre-roll

| Stage | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Beauty read | 8.763 / 9.757 / 10.615 | 166.380 / 168.411 / 169.286 |
| Vector read | 3.387 / 3.589 / 4.291 | 129.881 / 131.602 / 134.907 |
| Matte read | 4.044 / 4.123 / 4.495 | 121.086 / 122.414 / 123.894 |
| Total input read | 16.193 / 17.468 / 19.401 | 419.378 / 423.302 / 425.181 |
| Core processing | 1984.504 / 2027.317 / 2054.905 | 280.378 / 313.243 / 339.038 |
| Processed EXR write | 31.091 / 32.208 / 54.876 | 31.215 / 33.015 / 57.117 |
| Recovery-manifest commit | 0.391 / 0.399 / 0.422 | 0.399 / 0.402 / 0.418 |
| Diagnostics-report commit | 0.356 / 0.362 / 0.392 | 0.381 / 0.392 / 3.350 |
| Complete frame | 2056.387 / 2076.731 / 2107.377 | 738.740 / 766.504 / 822.196 |

### nonzero refresh

| Stage | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Beauty read | 9.656 / 9.895 / 10.599 | 166.369 / 166.762 / 167.299 |
| Vector read | 3.458 / 3.494 / 3.733 | 129.922 / 130.592 / 132.165 |
| Matte read | 3.864 / 4.027 / 4.077 | 119.779 / 121.429 / 122.884 |
| Total input read | 17.141 / 17.491 / 18.170 | 418.114 / 418.313 / 420.775 |
| Core processing | 1969.514 / 1981.297 / 2033.472 | 285.748 / 297.629 / 301.324 |
| Processed EXR write | 48.803 / 53.409 / 70.099 | 28.456 / 28.867 / 29.322 |
| Recovery-manifest commit | 0.387 / 0.550 / 1.809 | 0.381 / 0.392 / 0.399 |
| Diagnostics-report commit | 0.341 / 0.386 / 0.411 | 0.372 / 0.378 / 0.382 |
| Complete frame | 2041.074 / 2070.360 / 2102.049 | 735.821 / 745.912 / 749.340 |

## Memory evidence and limits

Both committed evidence files contain the same representative live-array definition: beauty RGBA,
Vector RGBA, matte, history RGBA, and history matte, all 1920×1080 float32. That footprint is
**116,121,600 bytes (110.74 MiB)** at both revisions. In isolated Blender benchmark processes,
`resource.getrusage(RUSAGE_SELF).ru_maxrss` recorded a process peak of **1,009,270,784 bytes
(962.52 MiB)** at PERF-1 and **994,787,328 bytes (948.70 MiB)** final:
**-1.44% (-13.81 MiB)**.

This is auditable and directly comparable because the harness hash, machine, operation order, fixture,
and process scope match. It is still a process-wide high-water mark, not a per-stage allocation
profile: Blender, generated EXRs, decoder buffers, Python/NumPy allocators, and prior workloads in
the fixed order are included. It cannot identify transient ownership or extrapolate peak memory to
a 147-frame run; recursive processing retains only the current history, but allocator behavior is
implementation-dependent.

## Correctness and release gate

The deterministic roadmap fixtures and full regression suite remain bit-for-bit equal (maximum
numerical error **0**). Coverage includes Full Frame + Trail, Extreme Hard, Target Only,
background-only pre-roll, nonzero deterministic refresh, resume/recovery and malformed history,
partial edge blocks, output/state/coverage and diagnostics equivalence, Blender orientation,
temporary image/data cleanup, and every roadmap benchmark contract. The actual Blender smoke
fixture and calibration scene are separate from this synthetic benchmark.

Verification commands and current results are recorded in PR #89. The prescribed gate is:

```text
uv run ty check
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
"$BLENDER_BIN" --background --factory-startup --python tests/blender_smoke_test.py
"$BLENDER_BIN" --background --factory-startup --python tests/create_calibration_scene.py
"$BLENDER_BIN" --command extension validate src/object_datamosh
mkdir -p dist
"$BLENDER_BIN" --command extension build --source-dir src/object_datamosh --output-dir dist
```

## Bottleneck and recommendation

For final Extreme Full Frame + Trail medians, input reads consume **417.300 ms (54.9%)**, core
processing **293.356 ms (38.6%)**, and output writing **34.712 ms (4.6%)** of the **760.567 ms**
complete frame. Manifest and diagnostics commits together are below 1 ms. The read regression is
real: PERF-1 used Blender loading for simple generated EXRs, while final production routing uses the
bundled decoder and transfer path. The exact same generated fixtures and production calls are used
at both revisions, so this is a cumulative release comparison rather than an isolated storage test.

**Recommended next direction: more CPU work focused narrowly on bundled EXR decode and
copy/transfer.** Output-writer or GPU work is not supported as the next roadmap by these data.
Rendering the 3D scene happens before these passes exist and is excluded. Remaining risks are that
the synthetic fixture does not model the reporter's storage, scene, compositor, or render engine;
RSS is not an allocation profile; and no interactive artistic/viewport inspection was performed.
