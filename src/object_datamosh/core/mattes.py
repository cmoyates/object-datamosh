"""Matte source contracts for sequence processing."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .paths import SequencePaths


class MatteProvider(Protocol):
    """Resolve the selected-object matte for a frame."""

    def path_for_frame(self, frame: int, sequence: SequencePaths) -> Path:
        """Return the matte image path for ``frame``."""
        ...


@dataclass(frozen=True, slots=True)
class CryptomatteMatteProvider:
    """Reserved provider contract for future Cryptomatte decoding."""

    def path_for_frame(self, frame: int, sequence: SequencePaths) -> Path:
        del frame, sequence
        raise NotImplementedError(
            "Cryptomatte decoding is experimental and is not available in the MVP"
        )


@dataclass(frozen=True, slots=True)
class ExternalMatteProvider:
    """Resolve a user-supplied numbered matte sequence."""

    directory: Path
    prefix: str = "matte_"
    extension: str = ".exr"
    padding: int = 4

    def path_for_frame(self, frame: int, sequence: SequencePaths) -> Path:
        del sequence
        return self.directory / f"{self.prefix}{frame:0{self.padding}d}{self.extension}"


@dataclass(frozen=True, slots=True)
class ObjectIndexMatteProvider:
    """Use the Object Index matte emitted with the raw pass sequence."""

    def path_for_frame(self, frame: int, sequence: SequencePaths) -> Path:
        return sequence.frame(frame).matte
