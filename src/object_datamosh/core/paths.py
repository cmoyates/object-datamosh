"""Deterministic paths shared by rendering and sequence processing."""

import logging
from dataclasses import dataclass
from numbers import Integral
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FramePaths:
    """All pass paths belonging to one sequence frame."""

    frame: int
    beauty: Path
    vector: Path
    matte: Path
    processed: Path


@dataclass(frozen=True, slots=True)
class SequencePaths:
    """Output directory contract for one blend file."""

    root: Path
    warning: str | None = None
    frame_padding: int = 4

    def __post_init__(self) -> None:
        _validate_padding(self.frame_padding)

    @classmethod
    def from_blend_file(
        cls,
        blend_file: str | Path,
        *,
        temp_directory: str | Path,
        frame_padding: int = 4,
    ) -> "SequencePaths":
        blend_path = Path(blend_file)
        if not blend_path.is_absolute():
            return cls(
                root=Path(temp_directory) / "ODM_object_datamosh_unsaved",
                warning="Save the blend file to use a project-relative output directory.",
                frame_padding=frame_padding,
            )
        root = blend_path.parent / f"ODM_{blend_path.stem}_object_datamosh"
        return cls(root=root, frame_padding=frame_padding)

    def frame(self, frame: int) -> FramePaths:
        _validate_frame(frame)
        token = f"{frame:0{self.frame_padding}d}"
        paths = FramePaths(
            frame=frame,
            beauty=self.root / "raw" / "beauty" / f"ODM_beauty_{token}.exr",
            vector=self.root / "raw" / "vector" / f"ODM_vector_{token}.exr",
            matte=self.root / "raw" / "matte" / f"ODM_matte_{token}.exr",
            processed=self.root / "processed" / f"ODM_processed_{token}.exr",
        )
        logging.getLogger(__name__).debug(
            "Resolved frame %d paths: beauty=%s, vector=%s, matte=%s, processed=%s",
            frame,
            paths.beauty,
            paths.vector,
            paths.matte,
            paths.processed,
        )
        return paths


def _validate_frame(frame: int) -> None:
    if isinstance(frame, bool) or not isinstance(frame, Integral):
        raise TypeError("frame must be an integer")


def _validate_padding(padding: int) -> None:
    if isinstance(padding, bool) or not isinstance(padding, Integral):
        raise TypeError("frame_padding must be an integer")
    if padding < 0:
        raise ValueError("frame_padding must not be negative")
