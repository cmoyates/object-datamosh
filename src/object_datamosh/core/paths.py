"""Deterministic paths shared by rendering and sequence processing."""

from dataclasses import dataclass
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

    @classmethod
    def from_blend_file(
        cls,
        blend_file: str | Path,
        *,
        temp_directory: str | Path,
        frame_padding: int = 4,
    ) -> "SequencePaths":
        if not str(blend_file):
            return cls(
                root=Path(temp_directory) / "object_datamosh_unsaved",
                warning="Save the blend file to use a project-relative output directory.",
                frame_padding=frame_padding,
            )
        blend_path = Path(blend_file)
        root = blend_path.parent / f"{blend_path.stem}_object_datamosh"
        return cls(root=root, frame_padding=frame_padding)

    def frame(self, frame: int) -> FramePaths:
        token = f"{frame:0{self.frame_padding}d}"
        return FramePaths(
            frame=frame,
            beauty=self.root / "raw" / "beauty" / f"beauty_{token}.exr",
            vector=self.root / "raw" / "vector" / f"vector_{token}.exr",
            matte=self.root / "raw" / "matte" / f"matte_{token}.exr",
            processed=self.root / "processed" / f"processed_{token}.exr",
        )
