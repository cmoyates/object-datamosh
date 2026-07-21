from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

import numpy as np
import pytest

from object_datamosh.core.exr import (
    InvalidOpenEXRError,
    UnsupportedOpenEXRError,
    _undo_zip_preprocessing,
    read_full_float_rgba,
)


def _reference_undo_zip_preprocessing(value: bytes) -> bytes:
    predicted = bytearray(value)
    for index in range(1, len(predicted)):
        predicted[index] = (predicted[index - 1] + predicted[index] - 128) & 0xFF
    half = (len(predicted) + 1) // 2
    restored = bytearray(len(predicted))
    restored[0::2] = predicted[:half]
    restored[1::2] = predicted[half:]
    return bytes(restored)


def _reference_zip_preprocessing(value: bytes) -> bytes:
    """Apply the inverse of the scalar decoder above as a test-only EXR writer."""
    reordered = value[0::2] + value[1::2]
    if not reordered:
        return b""
    encoded = bytearray(len(reordered))
    encoded[0] = reordered[0]
    for index in range(1, len(reordered)):
        encoded[index] = (reordered[index] - reordered[index - 1] + 128) & 0xFF
    return bytes(encoded)


def _attribute(name: str, attribute_type: str, value: bytes) -> bytes:
    return (
        name.encode("ascii")
        + b"\0"
        + attribute_type.encode("ascii")
        + b"\0"
        + struct.pack("<I", len(value))
        + value
    )


def _small_multilayer_exr(
    pixels: np.ndarray,
    *,
    compression: int,
    pixel_type: int = 2,
    channel_components: tuple[tuple[str, int], ...] = (
        ("Image.A", 3),
        ("Image.B", 2),
        ("Image.G", 1),
        ("Image.R", 0),
    ),
    version_flags: int = 0,
) -> bytes:
    """Construct a deterministic subset of Blender's scanline EXR layout."""
    height, width, _components = pixels.shape
    channel_list = (
        b"".join(
            name.encode("ascii") + b"\0" + struct.pack("<iB3xii", pixel_type, 0, 1, 1)
            for name, _component in channel_components
        )
        + b"\0"
    )
    data_window = struct.pack("<4i", 0, 0, width - 1, height - 1)
    header = (
        b"v/1\x01"
        + struct.pack("<I", 2 | version_flags)
        + _attribute("channels", "chlist", channel_list)
        + _attribute("compression", "compression", bytes([compression]))
        + _attribute("dataWindow", "box2i", data_window)
        + _attribute("displayWindow", "box2i", data_window)
        + _attribute("lineOrder", "lineOrder", b"\0")
        + _attribute("pixelAspectRatio", "float", struct.pack("<f", 1.0))
        + _attribute("screenWindowCenter", "v2f", struct.pack("<2f", 0.0, 0.0))
        + _attribute("screenWindowWidth", "float", struct.pack("<f", 1.0))
        + (
            _attribute("tiles", "tiledesc", struct.pack("<IIB", width, height, 0))
            if version_flags & 0x00000200
            else b""
        )
        + b"\0"
    )
    lines_per_block = {2: 1, 3: 16}.get(compression, 1)
    blocks: list[bytes] = []
    for first_y in range(0, height, lines_per_block):
        line_count = min(lines_per_block, height - first_y)
        unpacked = b"".join(
            pixels[y, :, component].astype("<f4", copy=False).tobytes()
            for y in range(first_y, first_y + line_count)
            for _name, component in channel_components
        )
        packed = zlib.compress(_reference_zip_preprocessing(unpacked))
        assert len(packed) < len(unpacked), "fixture must exercise compressed ZIP data"
        blocks.append(struct.pack("<iI", first_y, len(packed)) + packed)

    offset_table_size = math.ceil(height / lines_per_block) * 8
    first_block_offset = len(header) + offset_table_size
    offsets: list[int] = []
    next_offset = first_block_offset
    for block in blocks:
        offsets.append(next_offset)
        next_offset += len(block)
    return header + struct.pack(f"<{len(offsets)}Q", *offsets) + b"".join(blocks)


def _exr_header_end(fixture: bytes | bytearray) -> int:
    position = 8
    while fixture[position] != 0:
        position = fixture.index(0, position) + 1
        position = fixture.index(0, position) + 1
        size = struct.unpack_from("<I", fixture, position)[0]
        position += 4 + size
    return position + 1


def _representable_pass_pixels(pass_name: str, *, height: int = 17, width: int = 32) -> np.ndarray:
    values = {
        "beauty": (0.0, 0.25, 0.5, 1.0),
        "Vector": (-2.0, 0.5, 4.0, 1.0),
        "matte": (0.0, 0.0, 0.0, 1.0),
    }[pass_name]
    pixels = np.empty((height, width, 4), dtype=np.float32)
    pixels[...] = np.asarray(values, dtype=np.float32)
    pixels[::2, ::3] = np.asarray(values[::-1], dtype=np.float32)
    if pass_name == "matte":
        pixels[::2, ::3, :3] = 1.0
    return pixels


@pytest.mark.parametrize(
    "predicted",
    [
        b"",
        b"\x00",
        b"\xff",
        bytes([0] * 32),
        bytes([255] * 32),
        bytes([0, 255] * 17),
        bytes(range(31)),
        bytes(range(32)),
    ],
)
def test_zip_predictor_reversal_matches_reference_for_edge_patterns(predicted: bytes) -> None:
    assert _undo_zip_preprocessing(predicted) == _reference_undo_zip_preprocessing(predicted)


def test_zip_predictor_reversal_matches_reference_for_random_lengths() -> None:
    rng = np.random.default_rng(73073)

    for length in (*range(65), 127, 128, 129, 1023, 1024, 1025, 65_537):
        predicted = rng.integers(0, 256, size=length, dtype=np.uint8).tobytes()

        assert _undo_zip_preprocessing(predicted) == _reference_undo_zip_preprocessing(predicted)


def test_read_full_float_rgba_decodes_reference_zip_layout_bit_identically(
    tmp_path: Path,
) -> None:
    expected = _representable_pass_pixels("beauty")
    path = tmp_path / "ODM_beauty_zip.exr"
    fixture_pixels = _representable_pass_pixels("beauty")
    path.write_bytes(_small_multilayer_exr(fixture_pixels, compression=3))

    np.testing.assert_array_equal(read_full_float_rgba(path), expected)


def test_read_full_float_rgba_decodes_regular_zip_rgba_bit_identically(tmp_path: Path) -> None:
    expected = _representable_pass_pixels("beauty")
    path = tmp_path / "regular_rgba_zip.exr"
    path.write_bytes(
        _small_multilayer_exr(
            expected,
            compression=3,
            channel_components=(("A", 3), ("B", 2), ("G", 1), ("R", 0)),
        )
    )

    np.testing.assert_array_equal(read_full_float_rgba(path), expected)


def test_read_full_float_rgba_decodes_reference_zips_layout_bit_identically(
    tmp_path: Path,
) -> None:
    expected = _representable_pass_pixels("Vector")
    path = tmp_path / "ODM_vector_zips.exr"
    fixture_pixels = _representable_pass_pixels("Vector")
    path.write_bytes(_small_multilayer_exr(fixture_pixels, compression=2))

    np.testing.assert_array_equal(read_full_float_rgba(path), expected)


def test_read_full_float_rgba_decodes_reference_matte_bit_identically(tmp_path: Path) -> None:
    expected = _representable_pass_pixels("matte")
    path = tmp_path / "ODM_matte_zip.exr"
    fixture_pixels = _representable_pass_pixels("matte")
    path.write_bytes(_small_multilayer_exr(fixture_pixels, compression=3))

    np.testing.assert_array_equal(read_full_float_rgba(path), expected)


@pytest.mark.parametrize(
    ("compression", "pixel_type", "channels", "version_flags", "message"),
    [
        (0, 2, None, 0, "compression 0"),
        (4, 2, None, 0, "compression 4"),
        (3, 1, None, 0, "full-float"),
        (3, 2, (("R", 0), ("G", 1), ("B", 2)), 0, "RGBA"),
        (3, 2, None, 0x00000200, "Tiled"),
    ],
)
def test_read_full_float_rgba_classifies_valid_unsupported_variants(
    tmp_path: Path,
    compression: int,
    pixel_type: int,
    channels: tuple[tuple[str, int], ...] | None,
    version_flags: int,
    message: str,
) -> None:
    path = tmp_path / "unsupported.exr"
    default_channels = (("Image.A", 3), ("Image.B", 2), ("Image.G", 1), ("Image.R", 0))
    path.write_bytes(
        _small_multilayer_exr(
            _representable_pass_pixels("beauty"),
            compression=compression,
            pixel_type=pixel_type,
            channel_components=channels or default_channels,
            version_flags=version_flags,
        )
    )

    with pytest.raises(UnsupportedOpenEXRError, match=message):
        read_full_float_rgba(path)


@pytest.mark.parametrize("version_flags", [0, 0x00000200])
def test_read_full_float_rgba_classifies_corrupt_header(tmp_path: Path, version_flags: int) -> None:
    path = tmp_path / "corrupt_header.exr"
    path.write_bytes(b"v/1\x01" + struct.pack("<I", 2 | version_flags) + b"channels\0chlist\0")

    with pytest.raises(InvalidOpenEXRError, match="header"):
        read_full_float_rgba(path)


def test_read_full_float_rgba_rejects_mixed_multilayer_channels(tmp_path: Path) -> None:
    path = tmp_path / "mixed_layers.exr"
    path.write_bytes(
        _small_multilayer_exr(
            _representable_pass_pixels("beauty"),
            compression=3,
            channel_components=(
                ("left.A", 3),
                ("left.B", 2),
                ("right.G", 1),
                ("right.R", 0),
            ),
        )
    )

    with pytest.raises(UnsupportedOpenEXRError, match="RGBA"):
        read_full_float_rgba(path)


def test_read_full_float_rgba_preserves_ordinary_io_errors(tmp_path: Path) -> None:
    missing = tmp_path / "missing.exr"

    with pytest.raises(FileNotFoundError):
        read_full_float_rgba(missing)


def test_read_full_float_rgba_classifies_truncated_attribute_value(tmp_path: Path) -> None:
    path = tmp_path / "truncated_attribute.exr"
    path.write_bytes(
        b"v/1\x01"
        + struct.pack("<I", 2)
        + b"channels\0chlist\0"
        + struct.pack("<I", 128)
        + b"short"
    )

    with pytest.raises(InvalidOpenEXRError, match="header"):
        read_full_float_rgba(path)


def test_read_full_float_rgba_classifies_truncated_scanline_table(tmp_path: Path) -> None:
    path = tmp_path / "truncated_table.exr"
    fixture = _small_multilayer_exr(_representable_pass_pixels("beauty"), compression=3)
    path.write_bytes(fixture[: _exr_header_end(fixture) + 1])

    with pytest.raises(InvalidOpenEXRError, match="scanline table is truncated"):
        read_full_float_rgba(path)


def test_read_full_float_rgba_rejects_out_of_range_scanline_offset(tmp_path: Path) -> None:
    path = tmp_path / "invalid_offset.exr"
    fixture = bytearray(_small_multilayer_exr(_representable_pass_pixels("beauty"), compression=3))
    struct.pack_into("<Q", fixture, _exr_header_end(fixture), (1 << 64) - 1)
    path.write_bytes(fixture)

    with pytest.raises(InvalidOpenEXRError, match="scanline block is truncated"):
        read_full_float_rgba(path)


def test_read_full_float_rgba_rejects_truncated_zip_data_clearly(tmp_path: Path) -> None:
    path = tmp_path / "truncated_zip.exr"
    fixture = _small_multilayer_exr(_representable_pass_pixels("beauty"), compression=3)
    path.write_bytes(fixture[:-1])

    with pytest.raises(InvalidOpenEXRError, match="OpenEXR scanline block is invalid"):
        read_full_float_rgba(path)


def test_read_full_float_rgba_rejects_corrupt_zip_data_clearly(tmp_path: Path) -> None:
    path = tmp_path / "corrupt_zip.exr"
    fixture = bytearray(_small_multilayer_exr(_representable_pass_pixels("beauty"), compression=3))
    fixture[-1] ^= 0xFF  # Corrupt the final compressed block's Adler-32 checksum.
    path.write_bytes(fixture)

    with pytest.raises(InvalidOpenEXRError, match="OpenEXR ZIP block is invalid"):
        read_full_float_rgba(path)
