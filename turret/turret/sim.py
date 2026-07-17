"""Synthetic fly + synthetic sensors, so the whole turret runs headless.

The simulator owns the *ground truth*:
* a fly trajectory (drifting hover with jitter) inside the working volume,
* a "true" 3D-point -> DAC map (the physical galvo we are calibrating against),
* stereo detections (ground-truth projection + pixel noise),
* a photodiode window (a sine at the fly's wingbeat frequency + noise).

This lets us (a) fit the calibration from the true map, and (b) measure real
aiming error = |commanded DAC - true DAC needed to hit the fly's actual position|.
"""
from __future__ import annotations

import numpy as np

from .config import TurretConfig
from .detector import StereoDetection
from .geometry import StereoModel


class TrueGalvo:
    """Ground-truth physical galvo: 3D point -> DAC codes that would hit it.

    Beam angles are ax=atan(X/Z), ay=atan(Y/Z); DAC is an affine-plus-small-
    quadratic function of angle (real galvos are close to linear with mild
    nonlinearity). The calibration must learn this from samples.
    """

    def __init__(self, seed: int = 0):
        self.cx0, self.cy0 = 2048.0, 2048.0
        self.kx, self.ky = 5200.0, 5000.0      # DAC counts per radian
        self.kx2, self.ky2 = 900.0, -750.0     # mild nonlinearity

    def dac_for(self, xyz: np.ndarray) -> np.ndarray:
        x, y, z = float(xyz[0]), float(xyz[1]), float(xyz[2])
        ax, ay = np.arctan2(x, z), np.arctan2(y, z)
        dx = self.cx0 + self.kx * ax + self.kx2 * ax * ax
        dy = self.cy0 + self.ky * ay + self.ky2 * ay * ay
        return np.array([dx, dy])


class FlySimulator:
    def __init__(self, cfg: TurretConfig, wingbeat_hz: float = 220.0,
                 size_px: float = 12.0, seed: int = 0):
        self.cfg = cfg
        self.stereo = StereoModel(cfg.stereo)
        self.truth_galvo = TrueGalvo(seed)
        self.rng = np.random.default_rng(seed)
        self.wingbeat_hz = wingbeat_hz
        self.size_px = size_px
        self.t = 0.0
        # hover centre roughly in the middle of the working volume
        self.center = np.array([0.0, 0.0, 0.55])
        self._phase = self.rng.uniform(0, 2 * np.pi, size=3)

    def true_position(self, t: float) -> np.ndarray:
        """Slow lissajous hover with a little random drift — erratic but bounded."""
        amp = np.array([0.10, 0.06, 0.08])
        w = np.array([1.3, 1.7, 0.9])
        pos = self.center + amp * np.sin(w * t + self._phase)
        pos = pos + 0.004 * self.rng.standard_normal(3)  # jitter
        pos[2] = np.clip(pos[2], self.cfg.stereo.z_min_m + 0.02,
                         self.cfg.stereo.z_max_m - 0.02)
        return pos

    def wingbeat_window(self, n: int) -> np.ndarray:
        fs = self.cfg.wingbeat.sample_rate_hz
        t = np.arange(n) / fs
        sig = np.sin(2 * np.pi * self.wingbeat_hz * t)
        sig += 0.25 * self.rng.standard_normal(n)   # sensor noise
        return sig

    def step(self, dt: float, detect: bool = True):
        """Advance time; return (true_position, [StereoDetection] or [])."""
        self.t += dt
        pos = self.true_position(self.t)
        dets: list[StereoDetection] = []
        if detect:
            u_left, u_right, v = self.stereo.project(pos)
            noise = self.cfg.stereo  # 0.4 px detection noise
            u_left += 0.4 * self.rng.standard_normal()
            u_right += 0.4 * self.rng.standard_normal()
            v += 0.4 * self.rng.standard_normal()
            dets.append(StereoDetection(
                u_left=u_left, u_right=u_right, v=v,
                size_px=self.size_px,
                wingbeat=self.wingbeat_window(256),
            ))
        return pos, dets

    # ----- calibration data generation (mirrors the real DAC-grid sweep) -----
    def make_calibration_samples(self, n: int = 400):
        """Sample the working volume, return (points_xyz, dac_xy) using the true
        galvo — the sim analogue of sweeping DAC codes and detecting the dot."""
        c = self.cfg.stereo
        xs = self.rng.uniform(-0.18, 0.18, n)
        ys = self.rng.uniform(-0.14, 0.14, n)
        zs = self.rng.uniform(c.z_min_m, c.z_max_m, n)
        pts = np.stack([xs, ys, zs], axis=1)
        dacs = np.stack([self.truth_galvo.dac_for(p) for p in pts], axis=0)
        return pts, dacs
