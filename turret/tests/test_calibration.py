import numpy as np

from turret.calibration import GalvoCalibration
from turret.config import TurretConfig
from turret.sim import FlySimulator


def test_calibration_fits_true_galvo_and_generalizes():
    cfg = TurretConfig()
    sim = FlySimulator(cfg, seed=3)
    pts, dacs = sim.make_calibration_samples(600)

    # fit on the first 500, test on the held-out 100
    calib = GalvoCalibration(cfg.galvo.calib_degree).fit(pts[:500], dacs[:500])
    assert calib.residual_rms < 5.0  # near-exact fit over the workspace

    errs = []
    for p, true in zip(pts[500:], dacs[500:]):
        dx, dy = calib.predict(p)
        errs.append(np.hypot(dx - true[0], dy - true[1]))
    # held-out aiming error should be a handful of DAC counts (<< 4096 range)
    assert np.mean(errs) < 8.0


def test_predict_clamps_to_dac_range():
    cfg = TurretConfig()
    sim = FlySimulator(cfg, seed=0)
    pts, dacs = sim.make_calibration_samples(200)
    calib = GalvoCalibration().fit(pts, dacs)
    dx, dy = calib.predict(np.array([5.0, 5.0, 0.31]))  # way off axis
    assert 0 <= dx <= 4095 and 0 <= dy <= 4095


def test_fit_requires_matching_lengths():
    calib = GalvoCalibration()
    try:
        calib.fit(np.zeros((5, 3)), np.zeros((4, 2)))
    except ValueError:
        return
    raise AssertionError("expected ValueError on mismatched sample counts")
