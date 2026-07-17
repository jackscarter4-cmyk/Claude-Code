"""Camera/3D -> galvo-DAC calibration.

The turret never knows galvo angles analytically; it *learns* the map from a
target's 3D position to the DAC codes that land the beam on it. In the real
build you collect the samples by commanding a grid of DAC codes with the
bring-up laser and detecting where the dot lands (see design doc section 5.1).
Here that same (point -> dac) table is fitted with a low-order polynomial in the
reduced coordinates p = X/Z, q = Y/Z (which are proportional to the beam angles,
so a degree-2 fit is near-exact over a small workspace).
"""
from __future__ import annotations

import numpy as np


def _reduced_features(xyz: np.ndarray, degree: int) -> np.ndarray:
    """Polynomial features of the reduced coords p=X/Z, q=Y/Z.

    Accepts a single point (3,) or a batch (N,3); returns (M,) or (N,M).
    """
    xyz = np.asarray(xyz, dtype=float)
    single = xyz.ndim == 1
    if single:
        xyz = xyz[None, :]
    z = xyz[:, 2]
    if np.any(np.abs(z) < 1e-9):
        raise ValueError("Z too close to zero for reduced coordinates")
    p = xyz[:, 0] / z
    q = xyz[:, 1] / z
    cols = []
    for i in range(degree + 1):
        for j in range(degree + 1 - i):
            cols.append((p ** i) * (q ** j))
    feats = np.stack(cols, axis=1)
    return feats[0] if single else feats


class GalvoCalibration:
    """Least-squares polynomial map (X,Y,Z) -> (dac_x, dac_y)."""

    def __init__(self, degree: int = 2, dac_min: int = 0, dac_max: int = 4095):
        self.degree = int(degree)
        self.dac_min = int(dac_min)
        self.dac_max = int(dac_max)
        self.coeffs: np.ndarray | None = None   # (M, 2)
        self.residual_rms: float | None = None

    def fit(self, points_xyz: np.ndarray, dac_xy: np.ndarray) -> "GalvoCalibration":
        points_xyz = np.asarray(points_xyz, dtype=float)
        dac_xy = np.asarray(dac_xy, dtype=float)
        if points_xyz.shape[0] != dac_xy.shape[0]:
            raise ValueError("points and dac samples must have equal length")
        phi = _reduced_features(points_xyz, self.degree)          # (N, M)
        coeffs, *_ = np.linalg.lstsq(phi, dac_xy, rcond=None)     # (M, 2)
        self.coeffs = coeffs
        pred = phi @ coeffs
        self.residual_rms = float(np.sqrt(np.mean((pred - dac_xy) ** 2)))
        return self

    def predict(self, point_xyz: np.ndarray) -> tuple[int, int]:
        """Return clamped integer (dac_x, dac_y) to aim at a 3D point."""
        if self.coeffs is None:
            raise RuntimeError("calibration not fitted")
        phi = _reduced_features(np.asarray(point_xyz, dtype=float), self.degree)
        dac = phi @ self.coeffs
        dx = int(round(np.clip(dac[0], self.dac_min, self.dac_max)))
        dy = int(round(np.clip(dac[1], self.dac_min, self.dac_max)))
        return dx, dy
