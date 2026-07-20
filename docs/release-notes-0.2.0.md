# Object Datamosh 0.2.0 release notes

Object Datamosh 0.2.0 corrects and release-validates the CPU Extreme Full Frame workflow. It remains
an artistic temporal-feedback processor, not literal codec or compressed-bitstream corruption.

## Corrected workflow

- **Orientation:** all core arrays use top-left row zero, recorded as
  `display_top_left_v1`; Blender pixel-buffer conversion occurs only at the image-I/O boundary.
- **Configuration provenance:** schema-5 manifests expose complete readable `effective_settings`,
  including extension and Blender versions, while processing reports reference and verify the same
  semantic fingerprint.
- **Fallback:** Full Frame can use **Same Screen Position** history before current beauty when the
  primary motion sample is invalid. Target Only compatibility behavior is unchanged.
- **Trail:** Full Frame Trail supports screen-space/mixed Trail propagation. Trail Motion Follow
  `0` leaves coverage in screen space, `1` follows current motion, and intermediate values mix the
  bounded coverages.
- **Extreme preset:** Full Frame, Trail, Same Screen Position, Persistence `1.0`, Trail Decay
  `0.995`, Trail Motion Follow `0.1`, Refresh Probability `0.0`, Block Size `32`, Motion
  Quantization `8.0`, and Diffusion `6.0`.
- **Diagnostics:** the active-configuration summary and Target Only warning support preflight;
  bounded processing diagnostics report primary validity, same-pixel fallback, beauty fallback,
  refresh restoration, historical blending, changed output, resets, completion, and near-no-op
  warnings.

## Recovery and compatibility

Schema-v2 and other pre-schema-5 processed histories are not migrated or trusted because they
cannot prove orientation and all semantic controls. Retained raw beauty, Vector, and matte passes
can be reprocessed without rerendering. Target Only remains the global default, and strict resume
compatibility checks remain in force. See the
[corrected Extreme workflow migration guide](extreme-workflow-migration.md).

## Scope

This release adds no GPU processing, shader or backend abstraction, codec-corruption path, or
compiled runtime dependency. The corrected workflow remains Blender plus NumPy CPU processing.
