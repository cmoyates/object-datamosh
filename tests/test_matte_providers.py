from pathlib import Path
from typing import Any, cast

import pytest

from object_datamosh.core.mattes import (
    CryptomatteMatteProvider,
    ExternalMatteProvider,
    ObjectIndexMatteProvider,
)
from object_datamosh.core.paths import SequencePaths


def test_object_index_provider_uses_the_rendered_matte_for_each_frame(tmp_path: Path) -> None:
    sequence = SequencePaths(root=tmp_path / "output")
    provider = ObjectIndexMatteProvider()

    assert provider.path_for_frame(3, sequence) == (
        tmp_path / "output" / "raw" / "matte" / "ODM_matte_0003.exr"
    )


def test_external_provider_resolves_its_own_numbered_sequence(tmp_path: Path) -> None:
    sequence = SequencePaths(root=tmp_path / "output")
    provider = ExternalMatteProvider(directory=tmp_path / "mattes", prefix="mask_", padding=6)

    assert provider.path_for_frame(42, sequence) == tmp_path / "mattes" / "mask_000042.exr"


@pytest.mark.parametrize(
    ("keyword", "value", "message"),
    [
        ("prefix", "../../escape_", "prefix must be a single filename component"),
        ("prefix", "C:escape_", "prefix must be a single filename component"),
        ("extension", "../mask.exr", "extension must be a single filename component"),
        ("extension", "exr", "extension must start with a dot"),
    ],
)
def test_external_provider_rejects_unsafe_filename_parts(
    tmp_path: Path, keyword: str, value: str, message: str
) -> None:
    arguments = {keyword: value}

    with pytest.raises(ValueError, match=message):
        ExternalMatteProvider(directory=tmp_path, **cast(Any, arguments))


@pytest.mark.parametrize("padding", [-1, 1.5, True])
def test_external_provider_rejects_invalid_padding(tmp_path: Path, padding: object) -> None:
    error_type = ValueError if padding == -1 else TypeError
    with pytest.raises(error_type, match="padding"):
        ExternalMatteProvider(directory=tmp_path, padding=cast(Any, padding))


def test_external_provider_requires_integral_frames(tmp_path: Path) -> None:
    provider = ExternalMatteProvider(directory=tmp_path)
    sequence = SequencePaths(root=tmp_path / "output")

    with pytest.raises(TypeError, match="frame must be an integer"):
        provider.path_for_frame(cast(Any, 1.5), sequence)


def test_cryptomatte_provider_fails_explicitly_until_decoding_is_available(tmp_path: Path) -> None:
    sequence = SequencePaths(root=tmp_path / "output")
    provider = CryptomatteMatteProvider()

    with pytest.raises(NotImplementedError, match="Cryptomatte decoding is experimental"):
        provider.path_for_frame(1, sequence)
