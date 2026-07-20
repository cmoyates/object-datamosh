"""Pure-Python contracts shared by the Blender integration and processing core."""

import math
from dataclasses import dataclass
from enum import StrEnum
from numbers import Integral, Real

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


class FeedbackMode(StrEnum):
    """How selected-object history is localized in later frames."""

    HARD_LOCALIZED = "HARD_LOCALIZED"
    TRAIL = "TRAIL"


class HistorySource(StrEnum):
    """Which region of the previous processed frame may provide feedback color."""

    TARGET_ONLY = "TARGET_ONLY"
    FULL_FRAME = "FULL_FRAME"


@dataclass(frozen=True, slots=True)
class FeedbackState:
    """History carried between sequentially processed frames.

    ``history`` is scene-linear float32 RGBA with shape ``(height, width, 4)``.
    ``history_matte`` is float32 coverage with shape ``(height, width)``. It represents
    legal target-color coverage for Target Only history and independent effect/output
    coverage for Full Frame Trail history.
    """

    history: FloatImage
    history_matte: FloatMask
    frame_number: int

    def __post_init__(self) -> None:
        if not isinstance(self.history, np.ndarray):
            raise TypeError("history must be a NumPy array")
        if not isinstance(self.history_matte, np.ndarray):
            raise TypeError("history_matte must be a NumPy array")
        if self.history.dtype != np.float32:
            raise TypeError("history must use float32")
        if self.history_matte.dtype != np.float32:
            raise TypeError("history_matte must use float32")
        if self.history.ndim != 3 or self.history.shape[2] != 4:
            raise ValueError("history must have shape (height, width, 4)")
        if self.history_matte.shape != self.history.shape[:2]:
            raise ValueError("history_matte must have shape (height, width)")
        if isinstance(self.frame_number, bool) or not isinstance(self.frame_number, Integral):
            raise TypeError("frame_number must be an integer")


@dataclass(frozen=True, slots=True)
class FeedbackSettings:
    """Stable, Blender-independent controls for temporal feedback."""

    mode: FeedbackMode = FeedbackMode.HARD_LOCALIZED
    history_source: HistorySource = HistorySource.TARGET_ONLY
    trail_decay: float = 0.85
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
        for name in (
            "trail_decay",
            "persistence",
            "motion_gain",
            "motion_clamp",
            "motion_quantization",
            "diffusion",
            "refresh_probability",
        ):
            _require_finite_number(name, getattr(self, name))
        if isinstance(self.block_size, bool) or not isinstance(self.block_size, Integral):
            raise TypeError("block_size must be an integer")
        if isinstance(self.seed, bool) or not isinstance(self.seed, Integral):
            raise TypeError("seed must be an integer")
        if not isinstance(self.mode, FeedbackMode):
            raise TypeError("mode must be a FeedbackMode value")
        if not isinstance(self.history_source, HistorySource):
            raise TypeError("history_source must be a HistorySource value")
        if not isinstance(self.motion_channels, MotionChannels):
            raise TypeError("motion_channels must be a MotionChannels value")
        if not isinstance(self.matte_source, MatteSource):
            raise TypeError("matte_source must be a MatteSource value")
        for name in ("reverse_motion", "flip_x", "flip_y"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean")

        if not 0.0 <= self.trail_decay <= 1.0:
            raise ValueError("trail_decay must be between 0 and 1")
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


def _require_finite_number(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
