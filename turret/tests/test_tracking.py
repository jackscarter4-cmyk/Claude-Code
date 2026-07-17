import numpy as np

from turret.config import TrackerConfig
from turret.tracking import KalmanCV3D, Tracker


def test_kalman_converges_on_constant_velocity_target():
    cfg = TrackerConfig()
    rng = np.random.default_rng(0)
    p0 = np.array([0.0, 0.0, 0.5])
    vel = np.array([0.2, -0.1, 0.05])
    kf = KalmanCV3D(cfg, p0)
    dt = 1 / 60
    t = 0.0
    for _ in range(120):
        t += dt
        truth = p0 + vel * t
        meas = truth + rng.normal(0, 0.01, 3)
        kf.predict(dt)
        kf.update(meas)
    truth = p0 + vel * t
    assert np.linalg.norm(kf.position - truth) < 0.02
    # velocity is derived from noisy position, so allow a looser band; the point
    # is that it recovers the right velocity vector, not a perfect value
    assert np.linalg.norm(kf.velocity - vel) < 0.1


def test_lead_prediction_points_ahead():
    cfg = TrackerConfig()
    kf = KalmanCV3D(cfg, np.array([0.0, 0.0, 0.5]))
    kf.x[3:] = np.array([1.0, 0.0, 0.0])  # 1 m/s in +x
    lead = kf.predict_position(0.1)
    assert lead[0] > kf.position[0]
    assert abs(lead[0] - (kf.position[0] + 0.1)) < 1e-9


def test_tracker_locks_and_keeps_single_target():
    cfg = TrackerConfig()
    tr = Tracker(cfg)
    pos = np.array([0.0, 0.0, 0.5])
    locked = None
    for i in range(10):
        pos = pos + np.array([0.002, 0.0, 0.0])
        locked = tr.step([pos.copy()], 1 / 60)
    assert locked is not None
    first_id = locked.id
    # feed a few more frames; lock id must be stable
    for _ in range(10):
        pos = pos + np.array([0.002, 0.0, 0.0])
        locked = tr.step([pos.copy()], 1 / 60)
    assert locked.id == first_id


def test_track_dropped_after_misses():
    cfg = TrackerConfig()
    tr = Tracker(cfg)
    tr.step([np.array([0.0, 0.0, 0.5])], 1 / 60)
    assert len(tr.tracks) == 1
    for _ in range(cfg.max_misses + 2):
        tr.step([], 1 / 60)
    assert len(tr.tracks) == 0
