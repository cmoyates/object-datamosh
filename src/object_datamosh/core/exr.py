"""Minimal scanline OpenEXR decoding for Blender-emitted full-float RGBA passes."""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True, slots=True)
class _Channel:
    name: str
    pixel_type: int
    x_sampling: int
    y_sampling: int


def read_full_float_rgba(path: str | Path) -> np.ndarray:
    """Read a non-tiled, full-float RGBA OpenEXR using Blender's supported ZIP layouts.

    Blender 5.0's compositor emits ``OPEN_EXR_MULTILAYER`` files that its Image API identifies but
    does not expose through ``Image.pixels``. This decoder handles that narrow bundled-runtime
    contract without adding a compiled image dependency.
    """
    image_path = Path(path)
    data = image_path.read_bytes()
    attributes, position = _read_header(data, image_path)
    channels = _read_channels(attributes.get("channels", b""), image_path)
    if len(channels) != 4 or {channel.name.rsplit(".", 1)[-1] for channel in channels} != {
        "R",
        "G",
        "B",
        "A",
    }:
        raise ValueError(f"Expected an RGBA image at {image_path}, found {len(channels)} channels")
    if any(
        channel.pixel_type != 2 or channel.x_sampling != 1 or channel.y_sampling != 1
        for channel in channels
    ):
        raise ValueError(f"Expected a floating-point OpenEXR image at {image_path}")

    try:
        minimum_x, minimum_y, maximum_x, maximum_y = struct.unpack("<4i", attributes["dataWindow"])
        compression = attributes["compression"][0]
    except (KeyError, IndexError, struct.error) as error:
        raise ValueError(f"OpenEXR header is incomplete: {image_path}") from error
    width = maximum_x - minimum_x + 1
    height = maximum_y - minimum_y + 1
    if width <= 0 or height <= 0:
        raise ValueError(f"OpenEXR data window is invalid: {image_path}")
    lines_per_block = {0: 1, 2: 1, 3: 16}.get(compression)
    if lines_per_block is None:
        raise ValueError(f"Unsupported OpenEXR compression {compression} at {image_path}")

    block_count = math.ceil(height / lines_per_block)
    offset_table_end = position + block_count * 8
    if offset_table_end > len(data):
        raise ValueError(f"OpenEXR scanline table is truncated: {image_path}")
    offsets = struct.unpack_from(f"<{block_count}Q", data, position)
    result = np.empty((height, width, 4), dtype=np.float32)
    component_for_name = {"R": 0, "G": 1, "B": 2, "A": 3}

    for offset in offsets:
        try:
            y_coordinate, packed_size = struct.unpack_from("<iI", data, offset)
        except struct.error as error:
            raise ValueError(f"OpenEXR scanline block is truncated: {image_path}") from error
        packed_start = offset + 8
        packed = data[packed_start : packed_start + packed_size]
        line_count = min(lines_per_block, maximum_y - y_coordinate + 1)
        expected_size = line_count * width * len(channels) * 4
        if len(packed) != packed_size or line_count <= 0:
            raise ValueError(f"OpenEXR scanline block is invalid: {image_path}")
        if compression == 0 or packed_size == expected_size:
            unpacked = packed
        else:
            try:
                unpacked = _undo_zip_preprocessing(zlib.decompress(packed))
            except zlib.error as error:
                raise ValueError(f"OpenEXR ZIP block is invalid: {image_path}") from error
        if len(unpacked) != expected_size:
            raise ValueError(f"OpenEXR scanline block has an invalid size: {image_path}")

        byte_position = 0
        for line_offset in range(line_count):
            row = y_coordinate - minimum_y + line_offset
            if not 0 <= row < height:
                raise ValueError(f"OpenEXR scanline is outside its data window: {image_path}")
            for channel in channels:
                byte_end = byte_position + width * 4
                values = np.frombuffer(unpacked[byte_position:byte_end], dtype="<f4")
                component = component_for_name[channel.name.rsplit(".", 1)[-1]]
                result[row, :, component] = values
                byte_position = byte_end
    if not np.all(np.isfinite(result)):
        raise ValueError(f"OpenEXR image contains non-finite values: {image_path}")
    return np.ascontiguousarray(result, dtype=np.float32)


def _read_header(data: bytes, path: Path) -> tuple[dict[str, bytes], int]:
    if data[:4] != b"v/1\x01" or len(data) < 9:
        raise ValueError(f"Expected an OpenEXR image at {path}")
    version = struct.unpack_from("<I", data, 4)[0]
    if version & 0x00000200:
        raise ValueError(f"Tiled OpenEXR images are not supported: {path}")
    position = 8
    attributes: dict[str, bytes] = {}
    try:
        while data[position] != 0:
            name, position = _read_c_string(data, position)
            _attribute_type, position = _read_c_string(data, position)
            size = struct.unpack_from("<I", data, position)[0]
            position += 4
            attributes[name] = data[position : position + size]
            position += size
        position += 1
    except (IndexError, struct.error, UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"OpenEXR header is invalid: {path}") from error
    return attributes, position


def _read_channels(value: bytes, path: Path) -> tuple[_Channel, ...]:
    channels: list[_Channel] = []
    position = 0
    try:
        while value[position] != 0:
            name, position = _read_c_string(value, position)
            pixel_type = struct.unpack_from("<i", value, position)[0]
            x_sampling, y_sampling = struct.unpack_from("<ii", value, position + 8)
            position += 16
            channels.append(_Channel(name, pixel_type, x_sampling, y_sampling))
    except (IndexError, struct.error, UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"OpenEXR channel list is invalid: {path}") from error
    return tuple(channels)


def _read_c_string(data: bytes, position: int) -> tuple[str, int]:
    end = data.index(0, position)
    return data[position:end].decode("ascii"), end + 1


def _undo_zip_preprocessing(value: bytes) -> bytes:
    predicted = bytearray(value)
    for index in range(1, len(predicted)):
        predicted[index] = (predicted[index - 1] + predicted[index] - 128) & 0xFF
    half = (len(predicted) + 1) // 2
    restored = bytearray(len(predicted))
    restored[0::2] = predicted[:half]
    restored[1::2] = predicted[half:]
    return bytes(restored)
