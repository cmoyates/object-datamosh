import logging
from pathlib import Path

import pytest

from object_datamosh.core.paths import SequencePaths


def test_sequence_paths_derive_named_pass_files_from_a_saved_blend(tmp_path: Path) -> None:
    blend_file = tmp_path / "shot.blend"

    paths = SequencePaths.from_blend_file(blend_file, temp_directory=tmp_path / "temp")
    frame = paths.frame(12)

    assert paths.root == tmp_path / "ODM_shot_object_datamosh"
    assert paths.warning is None
    assert frame.beauty == paths.root / "raw" / "beauty" / "ODM_beauty_0012.exr"
    assert frame.vector == paths.root / "raw" / "vector" / "ODM_vector_0012.exr"
    assert frame.matte == paths.root / "raw" / "matte" / "ODM_matte_0012.exr"
    assert frame.processed == paths.root / "processed" / "ODM_processed_0012.exr"


@pytest.mark.parametrize("blend_file", ["", Path(""), "relative.blend"])
def test_sequence_paths_use_a_safe_temporary_root_for_an_unanchored_blend(
    tmp_path: Path, blend_file: str | Path
) -> None:
    paths = SequencePaths.from_blend_file(blend_file, temp_directory=tmp_path)

    assert paths.root == tmp_path / "ODM_object_datamosh_unsaved"
    assert paths.warning == "Save the blend file to use a project-relative output directory."


def test_frame_path_resolution_logs_frame_pass_and_path_details(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    paths = SequencePaths(root=tmp_path / "output")

    with caplog.at_level(logging.DEBUG, logger="object_datamosh.core.paths"):
        paths.frame(42)

    assert "Resolved frame 42 paths" in caplog.text
    assert "ODM_beauty_0042.exr" in caplog.text
    assert "ODM_vector_0042.exr" in caplog.text
    assert "ODM_matte_0042.exr" in caplog.text
    assert "ODM_processed_0042.exr" in caplog.text
