# CPU performance roadmap release validation (#79)

## Scope and result

This is the cumulative release record for roadmap #70. The verification-gap rerun compares the
original PERF-1 revision (`0b19e06bb690227b3a3f0711dbb1acf1e91d5563`) with integrated production
code at `ac356f8182ab6afc676553fccdb1303a7683c93a` on the same machine. Both runs used the exact same
benchmark script bytes (SHA-256
`a8361b01d1a7506bddfbef860683ee170bd0115af0c9252a107f07f9adde6113`), workload order, generated
1920×1080 float32 inputs, one warm-up per operation, one excluded sequence-path priming run, and
three measured samples. The JSON retains
each raw nanosecond sample as well as its count, median, range, and 147-frame extrapolation. This
supersedes the less-comparable workload coverage in the earlier release run.

For the canonical Extreme Full Frame + Trail non-reset frame, pure core fell from **2,162.123 ms**
to **300.185 ms** (**86.12% reduction / 7.20× speedup**) and measured complete-frame time fell from
**2,260.238 ms** to **759.081 ms** (**66.42% reduction / 2.98× speedup**). The latter gives a
median-only 147-frame processing estimate of **111.585 s (1.86 min)**, down from **332.255 s
(5.54 min)**. This is synthetic processing evidence on this machine, not a prediction for the
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
measurement, an exact output/state/coverage/diagnostics semantic signature, and every release stage
from the non-reset frame. Sequence measurements use one configured warm-up followed by one excluded
priming run of overwrite/recovery paths; each reported distribution then contains three samples. The
two-frame end-to-end range is reported as directly observed; the 147-frame values stored in the JSON
normalize its median by two.
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
| Statistics | 1 warm-up per operation; 1 excluded sequence priming run; 3 samples; minimum / median / maximum |

## Workload evidence

All values below are **minimum / median / maximum milliseconds** from the same harness. The pure
core table times one non-reset frame. “Invalid resumed history” instead times its required,
end-to-end expected rejection: after a valid sequence is committed, processed frame 2 is replaced
with a 960×540 image while its matte and sequence remain 1920×1080. Resume must reject that history
before core processing or output writing, so fabricated stage values would be misleading.

| Workload (pure core, except expected rejection) | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Extreme Full Frame + Trail | 2140.253 / 2162.123 / 2197.581 | 287.787 / 300.185 / 303.714 |
| Extreme Hard | 2062.114 / 2127.737 / 2134.358 | 239.842 / 240.249 / 244.164 |
| Target Only compatibility | 6525.387 / 6570.425 / 6652.797 | 322.296 / 338.342 / 340.102 |
| background-only pre-roll | 2025.470 / 2058.465 / 2087.290 | 282.667 / 301.699 / 302.601 |
| nonzero refresh | 2029.406 / 2065.255 / 2067.994 | 291.723 / 292.844 / 295.739 |
| invalid resumed history (expected rejection) | 20.387 / 20.713 / 20.898 | 463.491 / 463.734 / 468.486 |

The invalid-resume rejection is slower in final code because the production bundled EXR reader
decodes and validates the malformed history before rejecting its resolution. It still rejects before
frame processing, as required; this unfavorable result is not hidden.

| Successful workload (complete two-frame end to end) | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Extreme Full Frame + Trail | 2351.863 / 2376.469 / 2435.570 | 1241.921 / 1253.590 / 1291.731 |
| Extreme Hard | 2198.397 / 2279.153 / 2281.205 | 1205.300 / 1208.794 / 1228.601 |
| Target Only compatibility | 6476.177 / 6630.443 / 6639.819 | 1282.132 / 1305.143 / 1314.857 |
| background-only pre-roll | 2230.274 / 2251.248 / 2251.517 | 1265.958 / 1297.404 / 1325.650 |
| nonzero refresh | 2177.022 / 2198.105 / 2210.952 | 1259.200 / 1264.364 / 1275.284 |

The exact workload definitions are committed in both JSON files. All `FeedbackSettings` fields,
target pixel counts, frame count, fixture metadata, workload order, environment, and harness hash
are equal across revisions. The committed semantic signatures prove exact equality for processed
RGBA, next history RGBA, next effect-coverage matte, frame number, and every diagnostic counter for
each successful workload (maximum numerical error **0**).
Background-only pre-roll has zero target pixels on frame 1 and the normal target on frame 2;
nonzero refresh uses probability `0.25` and seed `73079`; Target Only uses compatibility defaults.

## Required release stages

Each table reports the successful workload's non-reset frame, again as **minimum / median / maximum
milliseconds**. “Total input read” is beauty + Vector + matte. “Complete frame” includes all listed
production stages and small orchestration overhead.

### Extreme Full Frame + Trail

| Stage | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Beauty read | 8.700 / 8.890 / 9.353 | 169.157 / 171.886 / 172.146 |
| Vector read | 3.524 / 3.555 / 3.795 | 131.716 / 132.113 / 132.557 |
| Matte read | 3.889 / 4.069 / 4.089 | 112.325 / 114.690 / 118.088 |
| Total input read | 16.145 / 16.774 / 16.946 | 416.187 / 416.405 / 422.087 |
| Core processing | 2152.477 / 2188.362 / 2241.077 | 296.536 / 305.566 / 334.075 |
| Processed EXR write | 54.539 / 55.751 / 61.810 | 28.087 / 31.050 / 36.201 |
| Recovery-manifest commit | 0.471 / 0.474 / 0.541 | 0.418 / 0.424 / 0.474 |
| Diagnostics-report commit | 0.388 / 0.441 / 0.564 | 0.371 / 0.398 / 0.405 |
| Complete frame | 2226.126 / 2260.238 / 2320.662 | 741.757 / 759.081 / 788.084 |

### Extreme Hard

| Stage | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Beauty read | 8.132 / 8.444 / 8.536 | 170.887 / 171.598 / 172.936 |
| Vector read | 3.518 / 3.539 / 4.005 | 131.955 / 133.589 / 138.220 |
| Matte read | 3.858 / 4.184 / 4.206 | 116.288 / 117.529 / 120.436 |
| Total input read | 15.995 / 16.146 / 16.281 | 420.371 / 421.476 / 431.592 |
| Core processing | 2049.338 / 2125.969 / 2136.495 | 248.744 / 255.257 / 263.762 |
| Processed EXR write | 29.866 / 31.285 / 32.308 | 29.894 / 30.535 / 31.798 |
| Recovery-manifest commit | 0.425 / 0.435 / 0.498 | 0.381 / 0.407 / 0.421 |
| Diagnostics-report commit | 0.388 / 0.414 / 0.422 | 0.386 / 0.409 / 3.339 |
| Complete frame | 2097.849 / 2172.874 / 2185.784 | 705.858 / 707.007 / 726.144 |

### Target Only compatibility

| Stage | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Beauty read | 8.214 / 8.902 / 9.289 | 170.770 / 171.263 / 174.193 |
| Vector read | 3.746 / 3.996 / 4.118 | 131.964 / 133.744 / 135.053 |
| Matte read | 4.285 / 4.328 / 4.951 | 114.924 / 119.942 / 122.410 |
| Total input read | 16.538 / 16.933 / 18.358 | 422.677 / 422.861 / 428.726 |
| Core processing | 6310.471 / 6434.918 / 6465.572 | 326.288 / 347.173 / 350.325 |
| Processed EXR write | 35.131 / 45.360 / 47.140 | 31.011 / 32.061 / 32.192 |
| Recovery-manifest commit | 0.445 / 0.591 / 0.634 | 0.396 / 0.397 / 0.453 |
| Diagnostics-report commit | 0.416 / 0.446 / 0.469 | 0.397 / 0.428 / 0.437 |
| Complete frame | 6363.277 / 6499.581 / 6530.836 | 782.066 / 806.215 / 807.794 |

### background-only pre-roll

| Stage | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Beauty read | 9.209 / 9.275 / 9.924 | 169.539 / 171.013 / 172.046 |
| Vector read | 3.432 / 3.610 / 3.694 | 132.159 / 132.630 / 137.024 |
| Matte read | 3.845 / 4.049 / 4.176 | 117.950 / 118.021 / 121.369 |
| Total input read | 16.748 / 16.756 / 17.709 | 420.190 / 424.540 / 427.020 |
| Core processing | 2037.297 / 2061.816 / 2061.831 | 299.682 / 300.336 / 320.124 |
| Processed EXR write | 50.924 / 53.669 / 56.677 | 29.902 / 53.769 / 115.289 |
| Recovery-manifest commit | 0.377 / 0.389 / 0.411 | 0.405 / 0.406 / 0.497 |
| Diagnostics-report commit | 0.387 / 0.401 / 0.535 | 0.398 / 0.437 / 0.439 |
| Complete frame | 2112.533 / 2130.520 / 2133.091 | 777.932 / 778.925 / 836.836 |

### nonzero refresh

| Stage | PERF-1 (ms) | Final (ms) |
| --- | ---: | ---: |
| Beauty read | 7.793 / 8.071 / 8.102 | 169.853 / 171.170 / 175.562 |
| Vector read | 3.387 / 3.448 / 3.631 | 133.605 / 134.635 / 136.552 |
| Matte read | 3.682 / 3.841 / 3.910 | 118.236 / 118.312 / 120.724 |
| Total input read | 14.923 / 15.299 / 15.643 | 424.041 / 424.182 / 430.426 |
| Core processing | 2033.828 / 2059.458 / 2070.355 | 291.623 / 293.441 / 295.505 |
| Processed EXR write | 29.132 / 29.436 / 30.298 | 31.955 / 36.167 / 48.425 |
| Recovery-manifest commit | 0.392 / 0.407 / 0.427 | 0.377 / 0.418 / 0.421 |
| Diagnostics-report commit | 0.347 / 0.358 / 0.411 | 0.375 / 0.409 / 0.431 |
| Complete frame | 2079.519 / 2104.648 / 2116.775 | 750.512 / 756.579 / 771.360 |

## Memory evidence and limits

Both committed evidence files contain the same representative live-array definition: beauty RGBA,
Vector RGBA, matte, history RGBA, and history matte, all 1920×1080 float32. That footprint is
**116,121,600 bytes (110.74 MiB)** at both revisions. In isolated Blender benchmark processes,
`resource.getrusage(RUSAGE_SELF).ru_maxrss` recorded a process peak of **1,027,948,544 bytes
(980.33 MiB)** at PERF-1 and **1,006,010,368 bytes (959.41 MiB)** final:
**-2.13% (-20.92 MiB)**.

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

For final Extreme Full Frame + Trail medians, input reads consume **416.405 ms (54.9%)**, core
processing **305.566 ms (40.3%)**, and output writing **31.050 ms (4.1%)** of the **759.081 ms**
complete frame. Manifest and diagnostics commits together are below 1 ms. The read regression is
real: PERF-1 used Blender loading for simple generated EXRs, while final production routing uses the
bundled decoder and transfer path. The exact same generated fixtures and production calls are used
at both revisions, so this is a cumulative release comparison rather than an isolated storage test.

**Recommended next direction: more CPU work focused narrowly on bundled EXR decode and
copy/transfer.** Output-writer or GPU work is not supported as the next roadmap by these data.
Rendering the 3D scene happens before these passes exist and is excluded. Remaining risks are that
the synthetic fixture does not model the reporter's storage, scene, compositor, or render engine;
RSS is not an allocation profile; and no interactive artistic/viewport inspection was performed.
