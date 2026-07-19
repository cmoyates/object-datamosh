"""Registered-operator integration check for background Blender."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, cast

import bpy
import numpy as np

import object_datamosh
from object_datamosh.blender_image_io import BlenderImageIO
from object_datamosh.core.paths import SequencePaths
from object_datamosh.ui import runtime_for_scene, settings_for_scene


def run_registered_modal_smoke(
    scene: bpy.types.Scene,
    image_io: BlenderImageIO,
    expected: np.ndarray,
) -> None:
    """Verify real bpy dispatch installs the modal operation and accepts cancellation."""
    object_datamosh.register()
    registered_root = Path(tempfile.mkdtemp(prefix="ODM_registered_modal_smoke_"))
    registered_paths = SequencePaths(registered_root)
    registered_frame = registered_paths.frame(1)
    image_io.write_rgba(registered_frame.beauty, expected)
    image_io.write_rgba(registered_frame.vector, np.zeros_like(expected))
    image_io.write_rgba(registered_frame.matte, np.ones_like(expected))
    settings = settings_for_scene(scene)
    settings.output_directory = str(registered_root)
    settings.frame_start = 1
    settings.frame_end = 1
    settings.matte_source = "OBJECT_INDEX"

    object_datamosh_ops = cast(Any, bpy.ops).object_datamosh
    assert object_datamosh_ops.process_sequence() == {"RUNNING_MODAL"}
    runtime = runtime_for_scene(scene)
    assert runtime.active
    assert runtime.phase == "PROCESSING"
    assert object_datamosh_ops.cancel_operation() == {"FINISHED"}
    assert runtime.cancel_requested
