from pathlib import Path

from object_datamosh.core.paths import SequencePaths


def test_sequence_paths_derive_named_pass_files_from_a_saved_blend(tmp_path: Path) -> None:
    blend_file = tmp_path / "shot.blend"

    paths = SequencePaths.from_blend_file(blend_file, temp_directory=tmp_path / "temp")
    frame = paths.frame(12)

    assert paths.root == tmp_path / "shot_object_datamosh"
    assert paths.warning is None
    assert frame.beauty == paths.root / "raw" / "beauty" / "beauty_0012.exr"
    assert frame.vector == paths.root / "raw" / "vector" / "vector_0012.exr"
    assert frame.matte == paths.root / "raw" / "matte" / "matte_0012.exr"
    assert frame.processed == paths.root / "processed" / "processed_0012.exr"


def test_sequence_paths_use_a_safe_temporary_root_for_an_unsaved_blend(tmp_path: Path) -> None:
    paths = SequencePaths.from_blend_file("", temp_directory=tmp_path)

    assert paths.root == tmp_path / "object_datamosh_unsaved"
    assert paths.warning == "Save the blend file to use a project-relative output directory."
