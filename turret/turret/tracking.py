"""Constant-velocity Kalman tracking + single-target lock.

Small fast insects plus ~30-50 ms of sense->aim latency mean you must aim where
the target *will be*, not where it was. Each track is a 6-state CV Kalman filter
[x,y,z,vx,vy,vz]; `predict_position(dt)` gives the lead point to aim at.
"""
from __future__ import annotations

import numpy as np

from .config import TrackerConfig


class KalmanCV3D:
    """6-state constant-velocity filter in 3D."""

    def __init__(self, cfg: TrackerConfig, init_pos: np.ndarray):
        self.cfg = cfg
        self.x = np.zeros(6)
        self.x[:3] = np.asarray(init_pos, dtype=float)
        # Large initial velocity uncertainty, modest position uncertainty.
        self.P = np.diag([1e-3, 1e-3, 1e-3, 10.0, 10.0, 10.0])
        self.R = np.eye(3) * cfg.meas_var_m2
        self.H = np.zeros((3, 6))
        self.H[0, 0] = self.H[1, 1] = self.H[2, 2] = 1.0

    def _F(self, dt: float) -> np.ndarray:
        F = np.eye(6)
        F[0, 3] = F[1, 4] = F[2, 5] = dt
        return F

    def _Q(self, dt: float) -> np.ndarray:
        qp, qv = self.cfg.process_pos_var, self.cfg.process_vel_var
        q = np.zeros((6, 6))
        # position noise grows with dt^2, velocity with dt; keep it simple & stable
        for i in range(3):
            q[i, i] = qp * dt * dt
            q[i + 3, i + 3] = qv * dt
        return q

    def predict(self, dt: float) -> None:
        F = self._F(dt)
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + self._Q(dt)

    def update(self, meas_xyz: np.ndarray) -> None:
        z = np.asarray(meas_xyz, dtype=float)
        y = z - self.H @ self.x
        S = self.H @ self.P @ self.H.T + self.R
        K = self.P @ self.H.T @ np.linalg.inv(S)
        self.x = self.x + K @ y
        self.P = (np.eye(6) - K @ self.H) @ self.P

    @property
    def position(self) -> np.ndarray:
        return self.x[:3].copy()

    @property
    def velocity(self) -> np.ndarray:
        return self.x[3:].copy()

    def predict_position(self, dt: float) -> np.ndarray:
        """Where the target will be dt seconds from now (no state mutation)."""
        return self.x[:3] + self.x[3:] * dt


class Track:
    _next_id = 1

    def __init__(self, cfg: TrackerConfig, pos: np.ndarray):
        self.id = Track._next_id
        Track._next_id += 1
        self.kf = KalmanCV3D(cfg, pos)
        self.hits = 1
        self.misses = 0
        self.age = 1

    @property
    def confirmed(self) -> bool:
        return self.hits >= self.kf.cfg.min_hits_to_lock


class Tracker:
    """Nearest-neighbour multi-target tracker that exposes one locked target."""

    def __init__(self, cfg: TrackerConfig):
        self.cfg = cfg
        self.tracks: list[Track] = []
        self.locked_id: int | None = None

    def step(self, detections_xyz: list[np.ndarray], dt: float) -> Track | None:
        # 1. predict all tracks forward
        for t in self.tracks:
            t.kf.predict(dt)
            t.age += 1

        # 2. greedy nearest-neighbour association within the gate
        unmatched = list(range(len(detections_xyz)))
        for t in self.tracks:
            best_j, best_d = None, self.cfg.gate_m
            for j in unmatched:
                d = float(np.linalg.norm(detections_xyz[j] - t.kf.position))
                if d < best_d:
                    best_d, best_j = d, j
            if best_j is not None:
                t.kf.update(detections_xyz[best_j])
                t.hits += 1
                t.misses = 0
                unmatched.remove(best_j)
            else:
                t.misses += 1

        # 3. spawn tracks for leftover detections
        for j in unmatched:
            self.tracks.append(Track(self.cfg, detections_xyz[j]))

        # 4. cull stale tracks
        self.tracks = [t for t in self.tracks if t.misses <= self.cfg.max_misses]

        # 5. maintain the lock: keep it if still alive & confirmed, else pick the
        #    oldest confirmed track (most stable) as the new lock.
        locked = self._get(self.locked_id)
        if locked is None or not locked.confirmed:
            confirmed = [t for t in self.tracks if t.confirmed]
            locked = max(confirmed, key=lambda t: t.age) if confirmed else None
            self.locked_id = locked.id if locked else None
        return locked

    def _get(self, tid: int | None) -> Track | None:
        if tid is None:
            return None
        for t in self.tracks:
            if t.id == tid:
                return t
        return None
