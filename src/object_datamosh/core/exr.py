"""Minimal scanline OpenEXR decoding for Blender-emitted full-float RGBA passes."""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np


class OpenEXRError(ValueError):
    """Base error for EXR files rejected by the bundled decoder."""


class UnsupportedOpenEXRError(OpenEXRError):
    """A valid EXR uses a feature outside the bundled decoder contract."""


class InvalidOpenEXRError(OpenEXRError):
    """An EXR is malformed, truncated, or contains invalid sample data."""


@dataclass(frozen=True, slots=True)
class _Channel:
    name: str
    pixel_type: int
    x_sampling: int
    y_sampling: int


def read_full_float_rgba(path: str | Path) -> np.ndarray:
    """Read a non-tiled, full-float RGBA OpenEXR using Blender's supported ZIP layouts.

    Blender's compositor emits ``OPEN_EXR_MULTILAYER`` files that its Image API identifies but
    does not expose through ``Image.pixels``. OpenEXR scanline Y increases from the displayed top,
    so scanline ``dataWindow.min.y`` maps directly to canonical NumPy row zero (top-left origin).
    This decoder handles that narrow bundled-runtime contract without a compiled dependency.
    """
    image_path = Path(path)
    data = image_path.read_bytes()
    attributes, position = _read_header(data, image_path)
    channels = _read_channels(attributes["channels"], image_path)
    try:
        minimum_x, minimum_y, maximum_x, maximum_y = struct.unpack("<4i", attributes["dataWindow"])
        compression = attributes["compression"][0]
    except (KeyError, IndexError, struct.error) as error:
        raise InvalidOpenEXRError(f"OpenEXR header is incomplete: {image_path}") from error
    width = maximum_x - minimum_x + 1
    height = maximum_y - minimum_y + 1
    if width <= 0 or height <= 0:
        raise InvalidOpenEXRError(f"OpenEXR data window is invalid: {image_path}")

    if any(
        channel.pixel_type not in {0, 1, 2} or channel.x_sampling <= 0 or channel.y_sampling <= 0
        for channel in channels
    ):
        raise InvalidOpenEXRError(f"OpenEXR channel metadata is invalid: {image_path}")
    channel_parts = [channel.name.rpartition(".") for channel in channels]
    channel_layers = {prefix if separator else "" for prefix, separator, _suffix in channel_parts}
    channel_components_found = {suffix for _prefix, _separator, suffix in channel_parts}
    if (
        len(channels) != 4
        or channel_components_found != {"R", "G", "B", "A"}
        or len(channel_layers) != 1
    ):
        raise UnsupportedOpenEXRError(
            f"Expected an RGBA image at {image_path}, found {len(channels)} channels"
        )
    if any(
        channel.pixel_type != 2 or channel.x_sampling != 1 or channel.y_sampling != 1
        for channel in channels
    ):
        raise UnsupportedOpenEXRError(f"Expected full-float OpenEXR channels at {image_path}")

    lines_per_block = {2: 1, 3: 16}.get(compression)
    if lines_per_block is None:
        raise UnsupportedOpenEXRError(
            f"Unsupported OpenEXR compression {compression} at {image_path}"
        )

    block_count = math.ceil(height / lines_per_block)
    offset_table_end = position + block_count * 8
    if offset_table_end > len(data):
        raise InvalidOpenEXRError(f"OpenEXR scanline table is truncated: {image_path}")
    offsets = struct.unpack_from(f"<{block_count}Q", data, position)
    result = np.empty((height, width, 4), dtype=np.float32)
    component_for_name = {"R": 0, "G": 1, "B": 2, "A": 3}
    channel_components = np.asarray(
        [component_for_name[channel.name.rsplit(".", 1)[-1]] for channel in channels]
    )
    rgba_channel_order = np.argsort(channel_components)
    populated_rows = np.zeros(height, dtype=bool)

    for offset in offsets:
        if offset > len(data) - 8:
            raise InvalidOpenEXRError(f"OpenEXR scanline block is truncated: {image_path}")
        try:
            y_coordinate, packed_size = struct.unpack_from("<iI", data, offset)
        except (OverflowError, struct.error) as error:
            raise InvalidOpenEXRError(
                f"OpenEXR scanline block is truncated: {image_path}"
            ) from error
        packed_start = offset + 8
        packed = data[packed_start : packed_start + packed_size]
        line_count = min(lines_per_block, maximum_y - y_coordinate + 1)
        expected_size = line_count * width * len(channels) * 4
        if len(packed) != packed_size or line_count <= 0:
            raise InvalidOpenEXRError(f"OpenEXR scanline block is invalid: {image_path}")
        if packed_size == expected_size:
            unpacked = packed
        else:
            try:
                unpacked = _undo_zip_preprocessing(zlib.decompress(packed))
            except zlib.error as error:
                raise InvalidOpenEXRError(f"OpenEXR ZIP block is invalid: {image_path}") from error
        if len(unpacked) != expected_size:
            raise InvalidOpenEXRError(f"OpenEXR scanline block has an invalid size: {image_path}")

        first_row = y_coordinate - minimum_y
        last_row = first_row + line_count
        if first_row < 0 or last_row > height or np.any(populated_rows[first_row:last_row]):
            raise InvalidOpenEXRError(
                f"OpenEXR scanline is outside or duplicated in its data window: {image_path}"
            )
        block_values = np.frombuffer(unpacked, dtype="<f4").reshape(
            line_count, len(channels), width
        )
        result[first_row:last_row] = block_values.transpose(0, 2, 1)[..., rgba_channel_order]
        populated_rows[first_row:last_row] = True
    if not np.all(populated_rows):
        raise InvalidOpenEXRError(f"OpenEXR scanline data is incomplete: {image_path}")
    if not np.all(np.isfinite(result)):
        raise InvalidOpenEXRError(f"OpenEXR image contains non-finite values: {image_path}")
    return np.ascontiguousarray(result, dtype=np.float32)


def _read_header(data: bytes, path: Path) -> tuple[dict[str, bytes], int]:
    if data[:4] != b"v/1\x01" or len(data) < 9:
        raise InvalidOpenEXRError(f"Expected an OpenEXR image at {path}")
    version = struct.unpack_from("<I", data, 4)[0]
    position = 8
    attributes: dict[str, bytes] = {}
    attribute_types: dict[str, str] = {}
    try:
        while data[position] != 0:
            name, position = _read_c_string(data, position)
            attribute_type, position = _read_c_string(data, position)
            size = struct.unpack_from("<I", data, position)[0]
            position += 4
            attribute_end = position + size
            if attribute_end > len(data):
                raise InvalidOpenEXRError(f"OpenEXR header is truncated: {path}")
            if not name or name in attributes:
                raise InvalidOpenEXRError(f"OpenEXR header has invalid attributes: {path}")
            attributes[name] = data[position:attribute_end]
            attribute_types[name] = attribute_type
            position = attribute_end
        position += 1
    except (IndexError, struct.error, UnicodeDecodeError, ValueError) as error:
        raise InvalidOpenEXRError(f"OpenEXR header is invalid: {path}") from error

    required_attributes = {
        "channels": ("chlist", None),
        "compression": ("compression", 1),
        "dataWindow": ("box2i", 16),
        "displayWindow": ("box2i", 16),
        "lineOrder": ("lineOrder", 1),
        "pixelAspectRatio": ("float", 4),
        "screenWindowCenter": ("v2f", 8),
        "screenWindowWidth": ("float", 4),
    }
    if version & 0x00000200:
        required_attributes["tiles"] = ("tiledesc", 9)
    for name, (expected_type, expected_size) in required_attributes.items():
        value = attributes.get(name)
        if (
            value is None
            or attribute_types.get(name) != expected_type
            or (expected_size is not None and len(value) != expected_size)
        ):
            raise InvalidOpenEXRError(f"OpenEXR header attribute {name!r} is invalid: {path}")
    compression = attributes["compression"][0]
    if compression > 9 or attributes["lineOrder"][0] > 2:
        raise InvalidOpenEXRError(f"OpenEXR header contains an invalid enum value: {path}")
    for window_name in ("dataWindow", "displayWindow"):
        minimum_x, minimum_y, maximum_x, maximum_y = struct.unpack("<4i", attributes[window_name])
        if maximum_x < minimum_x or maximum_y < minimum_y:
            raise InvalidOpenEXRError(
                f"OpenEXR header attribute {window_name!r} is invalid: {path}"
            )
    display_metadata = (
        attributes["pixelAspectRatio"]
        + attributes["screenWindowCenter"]
        + attributes["screenWindowWidth"]
    )
    scalar_values = struct.unpack("<f2ff", display_metadata)
    if (
        not all(math.isfinite(value) for value in scalar_values)
        or scalar_values[0] <= 0.0
        or scalar_values[3] <= 0.0
    ):
        raise InvalidOpenEXRError(f"OpenEXR header contains invalid display metadata: {path}")
    if version & 0x00000200:
        tile_width, tile_height, tile_mode = struct.unpack("<IIB", attributes["tiles"])
        if tile_width == 0 or tile_height == 0 or tile_mode & ~0x13 or (tile_mode & 0x0F) > 2:
            raise InvalidOpenEXRError(f"OpenEXR tile description is invalid: {path}")
        raise UnsupportedOpenEXRError(f"Tiled OpenEXR images are not supported: {path}")
    if version & (0x00000800 | 0x00001000):
        raise UnsupportedOpenEXRError(f"Deep or multipart OpenEXR images are not supported: {path}")
    if version & 0xFF != 2:
        raise UnsupportedOpenEXRError(f"OpenEXR version {version & 0xFF} is not supported: {path}")
    return attributes, position


def _read_channels(value: bytes, path: Path) -> tuple[_Channel, ...]:
    channels: list[_Channel] = []
    position = 0
    try:
        while value[position] != 0:
            name, position = _read_c_string(value, position)
            pixel_type, linear, reserved, x_sampling, y_sampling = struct.unpack_from(
                "<iB3sii", value, position
            )
            position += 16
            if not name or linear not in {0, 1} or reserved != b"\0\0\0":
                raise InvalidOpenEXRError(f"OpenEXR channel metadata is invalid: {path}")
            channels.append(_Channel(name, pixel_type, x_sampling, y_sampling))
        position += 1
        if position != len(value) or len({channel.name for channel in channels}) != len(channels):
            raise InvalidOpenEXRError(f"OpenEXR channel list is invalid: {path}")
    except (IndexError, struct.error, UnicodeDecodeError, ValueError) as error:
        raise InvalidOpenEXRError(f"OpenEXR channel list is invalid: {path}") from error
    return tuple(channels)


def _read_c_string(data: bytes, position: int) -> tuple[str, int]:
    end = data.index(0, position)
    return data[position:end].decode("ascii"), end + 1


def _undo_zip_preprocessing(value: bytes) -> bytes:
    """Reverse OpenEXR ZIP prediction and byte reordering."""
    if not value:
        return b""

    encoded = np.frombuffer(value, dtype=np.uint8)
    predicted = np.empty(encoded.size, dtype=np.uint8)
    predicted[0] = encoded[0]
    if encoded.size > 1:
        deltas = encoded[1:].astype(np.int64) - 128
        predicted[1:] = encoded[0].astype(np.int64) + np.cumsum(deltas, dtype=np.int64)

    half = (predicted.size + 1) // 2
    restored = np.empty_like(predicted)
    restored[0::2] = predicted[:half]
    restored[1::2] = predicted[half:]
    return restored.tobytes()
