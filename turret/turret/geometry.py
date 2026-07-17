"""Stereo geometry: turn a matched pair of image detections into a 3D point.

Standard rectified-pinhole triangulation. Left/right cameras share the same
row (v), so disparity is the horizontal pixel difference uL - uR.
"""
from __future__ import annotations

import numpy as np

from .config import StereoConfig


class StereoModel:
    def __init__(self, cfg: StereoConfig):
        self.cfg = cfg

    def triangulate(self, u_left: float, u_right: float, v: float) -> np.ndarray:
        """Return camera-frame [X, Y, Z] in metres for a matched detection.

        Raises ValueError if disparity is non-positive (target behind camera or
        a bad match) so the caller can reject it rather than emit garbage depth.
        """
        c = self.cfg
        disparity = float(u_left) - float(u_right)
        if disparity <= 1e-6:
            raise ValueError(f"non-positive disparity {disparity:.3f}")
        z = c.fx * c.baseline_m / disparity
        x = (u_left - c.cx) * z / c.fx
        y = (v - c.cy) * z / c.fy
        return np.array([x, y, z], dtype=float)

    def project(self, point_xyz: np.ndarray) -> tuple[float, float, float]:
        """Inverse of triangulate: 3D point -> (u_left, u_right, v). Used by the
        simulator and for camera<->galvo closed-loop checks."""
        c = self.cfg
        x, y, z = float(point_xyz[0]), float(point_xyz[1]), float(point_xyz[2])
        if z <= 1e-6:
            raise ValueError("point is at or behind the camera plane")
        u_left = c.fx * x / z + c.cx
        v = c.fy * y / z + c.cy
        disparity = c.fx * c.baseline_m / z
        u_right = u_left - disparity
        return u_left, u_right, v

    def in_working_volume(self, point_xyz: np.ndarray) -> bool:
        c = self.cfg
        z = float(point_xyz[2])
        return c.z_min_m <= z <= c.z_max_m
