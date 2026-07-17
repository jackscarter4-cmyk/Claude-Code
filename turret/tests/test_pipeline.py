import numpy as np

from turret.config import TurretConfig
from turret.pipeline import run_headless


def test_headless_loop_locks_tracks_and_aims_accurately():
    cfg = TurretConfig()
    summary = run_headless(cfg, n_steps=360, seed=1)

    # calibration should fit the (simulated) galvo almost exactly
    assert summary["calib_residual_rms_dac"] < 5.0

    # the turret should hold a lock on the fly the large majority of the time
    assert summary["locked_fraction"] > 0.85

    # steady-state aiming error should be small: a few mrad, well under a degree
    assert summary["approx_aim_error_mrad"] < 10.0

    # with all interlocks satisfied and a confirmed wingbeat, it should be
    # permitted to fire on many frames
    assert summary["fired_steps"] > 100


def test_estop_blocks_firing_in_loop():
    from turret.calibration import GalvoCalibration
    from turret.galvo import MockGalvo
    from turret.laser import MockLaser
    from turret.pipeline import TurretPipeline
    from turret.sim import FlySimulator

    cfg = TurretConfig()
    sim = FlySimulator(cfg, seed=2)
    pts, dacs = sim.make_calibration_samples(400)
    calib = GalvoCalibration().fit(pts, dacs)
    laser = MockLaser()
    pipe = TurretPipeline(cfg, calib, galvo=MockGalvo(), laser=laser)

    dt = 1 / cfg.loop_hz
    for _ in range(60):
        _, dets = sim.step(dt)
        pipe.step(dets, dt, key_on=True, enclosure_closed=True,
                  estop=True, backstop_present=True)
    assert laser.fire_count == 0     # e-stop must prevent every shot
