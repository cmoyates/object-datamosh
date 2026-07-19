"""Matte source contracts for sequence processing."""

from dataclasses import dataclass
from numbers import Integral
from pathlib import Path, PureWindowsPath
from typing import Protocol

from .paths import SequencePaths, format_frame_token


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

    def __post_init__(self) -> None:
        _validate_filename_part("prefix", self.prefix)
        _validate_filename_part("extension", self.extension)
        if not self.extension.startswith("."):
            raise ValueError("extension must start with a dot")
        _validate_padding(self.padding)

    def path_for_frame(self, frame: int, sequence: SequencePaths) -> Path:
        del sequence
        token = format_frame_token(frame, self.padding)
        return self.directory / f"{self.prefix}{token}{self.extension}"


@dataclass(frozen=True, slots=True)
class ObjectIndexMatteProvider:
    """Use the Object Index matte emitted with the raw pass sequence."""

    def path_for_frame(self, frame: int, sequence: SequencePaths) -> Path:
        return sequence.frame(frame).matte


def _validate_filename_part(name: str, value: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if "/" in value or "\\" in value or PureWindowsPath(value).drive or value in {".", ".."}:
        raise ValueError(f"{name} must be a single filename component")


def _validate_padding(padding: int) -> None:
    if isinstance(padding, bool) or not isinstance(padding, Integral):
        raise TypeError("padding must be an integer")
    if padding < 1:
        raise ValueError("padding must be at least one")
