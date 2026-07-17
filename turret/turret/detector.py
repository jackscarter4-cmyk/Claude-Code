"""Detection data type + detector interface.

The pipeline consumes `StereoDetection`s (a matched left/right blob plus a size
and a wing-beat signal window). How you produce them is swappable:

* `YoloStereoDetector` (real) — run a tiny YOLO on each frame, match blobs across
  the stereo pair by row, read the photodiode window. Imported lazily; needs
  opencv + a model, so it is not exercised in the headless tests.
* the simulator (`sim.py`) emits `StereoDetection`s directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass
class StereoDetection:
    u_left: float
    u_right: float
    v: float
    size_px: float                 # bbox diagonal, for the size safety gate
    wingbeat: np.ndarray           # photodiode window for this candidate


class Detector(Protocol):
    def detect(self) -> list[StereoDetection]: ...


class YoloStereoDetector:
    """Real detector skeleton. Kept import-light; fill in with your model."""

    def __init__(self, model_path: str, camera_pair, photodiode):
        import cv2  # type: ignore  # noqa: F401

        self.model_path = model_path
        self.cameras = camera_pair
        self.photodiode = photodiode
        # load your YOLO-n / YOLOv4-tiny here

    def detect(self) -> list[StereoDetection]:  # pragma: no cover - hardware path
        raise NotImplementedError(
            "Wire up YOLO inference + stereo row-matching + photodiode read here."
        )
