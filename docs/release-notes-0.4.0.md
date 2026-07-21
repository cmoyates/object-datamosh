# Object Datamosh 0.4.0 release notes

Object Datamosh 0.4.0 improves CPU performance for the Extreme Full Frame workflow while preserving
its artistic output, recursive history, recovery guarantees, and Blender integration contracts. It
remains an artistic temporal-feedback processor, not literal codec or compressed-bitstream
corruption.

## CPU and image-I/O improvements

- Zero-refresh diagnostics avoid unnecessary block scanning.
- OpenEXR ZIP predictor reversal is vectorized, and supported ZIP/ZIPS full-float RGBA inputs use
  the bundled decoder before Blender Image data-block fallback.
- Diagnostics reports use bounded checkpoints while recovery manifests remain atomic after every
  completed frame.
- Clean Full Frame history avoids unnecessary validity-mask sampling and Same Screen Position uses
  direct same-pixel fallback.
- Eligible empty-effect Hard and Trail frames skip motion preparation and historical sampling
  without weakening validation or recursive state.
- Reusable bilinear sampling plans were benchmarked and rejected because their memory and
  complexity costs were not justified.

The cumulative benchmark methodology, results, limitations, and next-direction recommendation are
recorded in the [CPU performance release validation](cpu-performance-release-validation.md).
Benchmarks are developer evidence from one measured machine, not performance guarantees for every
system.

## Preserved Full Frame contract

- Core arrays retain top-left row zero with orientation marker `display_top_left_v1`.
- Schema-5 manifests continue to expose complete `effective_settings` provenance.
- Full Frame can use **Same Screen Position** history before current beauty when the primary motion
  sample is invalid.
- Full Frame Trail retains screen-space/mixed Trail propagation.
- The Extreme preset remains Full Frame, Trail, Same Screen Position, Persistence `1.0`, Trail
  Decay `0.995`, Trail Motion Follow `0.1`, Refresh Probability `0.0`, Block Size `32`, Motion
  Quantization `8.0`, and Diffusion `6.0`.
- Bounded processing diagnostics retain their existing counters, warnings, and terminal truth.

## Recovery and compatibility

Target Only remains the global default. Resume compatibility and contamination checks remain
strict, processed frames remain recursively sequential, and recovery manifests remain atomic per
completed frame. Retained raw beauty, Vector, and matte passes can still be reprocessed without
rerendering the 3D scene.

## Scope

This release adds no GPU processing, frame-level parallelism, shader or backend abstraction,
codec-corruption path, or compiled runtime dependency. The extension continues to require only
Blender's bundled Python modules and NumPy.
