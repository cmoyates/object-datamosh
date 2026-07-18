"""Pure-Python contracts shared by the Blender integration and processing core."""

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from numpy.typing import NDArray

FloatImage = NDArray[np.float32]
FloatMask = NDArray[np.float32]


class MotionChannels(StrEnum):
    """Channel pair carrying horizontal and vertical motion."""

    RG = "RG"
    BA = "BA"


class MatteSource(StrEnum):
    """Supported ways to identify the selected object."""

    OBJECT_INDEX = "OBJECT_INDEX"
    EXTERNAL = "EXTERNAL"
    CRYPTOMATTE = "CRYPTOMATTE"


@dataclass(frozen=True, slots=True)
class FeedbackState:
    """History carried between sequentially processed frames.

    ``history`` is scene-linear float32 RGBA with shape ``(height, width, 4)``.
    ``history_matte`` is float32 coverage with shape ``(height, width)``.
    """

    history: FloatImage
    history_matte: FloatMask
    frame_number: int

    def __post_init__(self) -> None:
        if self.history.dtype != np.float32:
            raise TypeError("history must use float32")
        if self.history_matte.dtype != np.float32:
            raise TypeError("history_matte must use float32")
        if self.history.ndim != 3 or self.history.shape[2] != 4:
            raise ValueError("history must have shape (height, width, 4)")
        if self.history_matte.shape != self.history.shape[:2]:
            raise ValueError("history_matte must have shape (height, width)")


@dataclass(frozen=True, slots=True)
class FeedbackSettings:
    """Stable, Blender-independent controls for temporal feedback."""

    persistence: float = 0.85
    block_size: int = 16
    motion_channels: MotionChannels = MotionChannels.RG
    reverse_motion: bool = False
    flip_x: bool = False
    flip_y: bool = False
    motion_gain: float = 1.0
    motion_clamp: float = 64.0
    motion_quantization: float = 1.0
    diffusion: float = 0.0
    refresh_probability: float = 0.0
    seed: int = 0
    matte_source: MatteSource = MatteSource.OBJECT_INDEX

    def __post_init__(self) -> None:
        if not 0.0 <= self.persistence <= 1.0:
            raise ValueError("persistence must be between 0 and 1")
        if self.block_size < 1:
            raise ValueError("block_size must be at least 1")
        if self.motion_gain < 0.0:
            raise ValueError("motion_gain must not be negative")
        if self.motion_clamp < 0.0:
            raise ValueError("motion_clamp must not be negative")
        if self.motion_quantization < 0.0:
            raise ValueError("motion_quantization must not be negative")
        if self.diffusion < 0.0:
            raise ValueError("diffusion must not be negative")
        if not 0.0 <= self.refresh_probability <= 1.0:
            raise ValueError("refresh_probability must be between 0 and 1")
