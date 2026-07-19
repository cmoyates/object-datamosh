"""Isolated real-bpy dispatch smoke for the modal processing operator."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, cast

import bpy
import numpy as np

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import object_datamosh  # noqa: E402
from object_datamosh.blender_image_io import BlenderImageIO  # noqa: E402
from object_datamosh.core.paths import SequencePaths  # noqa: E402
from object_datamosh.ui import runtime_for_scene, settings_for_scene  # noqa: E402


def main() -> None:
    """Verify registered dispatch/timer setup in a process isolated from deterministic fakes."""
    object_datamosh.register()
    scene = bpy.context.scene
    assert scene is not None
    image_io = BlenderImageIO()
    expected = np.ones((1, 2, 4), dtype=np.float32)
    with tempfile.TemporaryDirectory(prefix="ODM_registered_modal_smoke_") as temporary:
        paths = SequencePaths(Path(temporary))
        frame = paths.frame(1)
        image_io.write_rgba(frame.beauty, expected)
        image_io.write_rgba(frame.vector, np.zeros_like(expected))
        image_io.write_rgba(frame.matte, expected)
        settings = settings_for_scene(scene)
        settings.output_directory = temporary
        settings.frame_start = 1
        settings.frame_end = 1
        settings.matte_source = "OBJECT_INDEX"

        operators = cast(Any, bpy.ops).object_datamosh
        assert operators.process_sequence() == {"RUNNING_MODAL"}
        runtime = runtime_for_scene(scene)
        assert runtime.active
        assert runtime.phase == "PROCESSING"
        assert operators.cancel_operation() == {"FINISHED"}
        assert runtime.cancel_requested
    # Background Blender cannot pump the foreground modal event while this script owns its main
    # thread. Process exit now releases the isolated timer/handler; deterministic finalization is
    # asserted in the parent smoke process using the same controller/lifecycle implementation.
    print("Registered modal dispatch smoke passed")


if __name__ == "__main__":
    main()
