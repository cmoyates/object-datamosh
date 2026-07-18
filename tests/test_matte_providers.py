from pathlib import Path

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
        tmp_path / "output" / "raw" / "matte" / "matte_0003.exr"
    )


def test_external_provider_resolves_its_own_numbered_sequence(tmp_path: Path) -> None:
    sequence = SequencePaths(root=tmp_path / "output")
    provider = ExternalMatteProvider(directory=tmp_path / "mattes", prefix="mask_", padding=6)

    assert provider.path_for_frame(42, sequence) == tmp_path / "mattes" / "mask_000042.exr"


def test_cryptomatte_provider_fails_explicitly_until_decoding_is_available(tmp_path: Path) -> None:
    sequence = SequencePaths(root=tmp_path / "output")
    provider = CryptomatteMatteProvider()

    with pytest.raises(NotImplementedError, match="Cryptomatte decoding is experimental"):
        provider.path_for_frame(1, sequence)
