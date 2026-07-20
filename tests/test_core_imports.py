import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.integration
def test_all_core_modules_import_without_blender() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(source_root)
    program = """
import sys
from object_datamosh.core import (
    block_preparation,
    contracts,
    feedback,
    image_io,
    mattes,
    ownership,
    paths,
    sampling,
)
assert 'bpy' not in sys.modules
print('core imported without bpy')
"""

    result = subprocess.run(
        [sys.executable, "-c", program],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "core imported without bpy\n"
