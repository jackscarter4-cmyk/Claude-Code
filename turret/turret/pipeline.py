"""The control loop that wires everything together.

Per tick:
  detections -> stereo 3D -> tracker (predict+update) -> pick locked target
  -> wing-beat gate -> safety evaluate -> aim galvo at LEAD point -> maybe fire.

`TurretPipeline.step()` is pure w.r.t. hardware: you hand it detections and the
hardware-condition booleans, it returns a telemetry dict. `run_headless()` drives
it from the simulator.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .calibration import GalvoCalibration
from .config import TurretConfig
from .detector import StereoDetection
from .galvo import GalvoController, MockGalvo
from .geometry import StereoModel
from .laser import MockLaser
from .safety import SafetyInputs, SafetyInterlock
from .tracking import Tracker
from .wingbeat import WingbeatGate


@dataclass
class Telemetry:
    locked: bool = False
    track_id: int | None = None
    target_xyz: np.ndarray | None = None
    lead_xyz: np.ndarray | None = None
    dac: tuple[int, int] | None = None
    wingbeat_hz: float = 0.0
    permit_fire: bool = False
    fired: bool = False
    reasons: list[str] = field(default_factory=list)


class TurretPipeline:
    def __init__(self, cfg: TurretConfig, calibration: GalvoCalibration,
                 galvo=None, laser=None):
        self.cfg = cfg
        self.stereo = StereoModel(cfg.stereo)
        self.tracker = Tracker(cfg.tracker)
        self.wingbeat = WingbeatGate(cfg.wingbeat)
        self.safety = SafetyInterlock(cfg.safety)
        self.galvo = GalvoController(galvo or MockGalvo(), calibration)
        self.laser = laser or MockLaser()

    def step(self, detections: list[StereoDetection], dt: float, *,
             key_on: bool, enclosure_closed: bool, estop: bool,
             backstop_present: bool) -> Telemetry:
        # 1. detections -> 3D points (drop bad disparities / out-of-volume)
        pts, det_by_pt = [], []
        for d in detections:
            try:
                p = self.stereo.triangulate(d.u_left, d.u_right, d.v)
            except ValueError:
                continue
            pts.append(p)
            det_by_pt.append(d)

        # 2. track & lock
        locked = self.tracker.step(pts, dt)
        tel = Telemetry()
        if locked is None:
            # nothing to aim at; keep laser off but still service safety latch
            self.safety.evaluate(SafetyInputs(estop_pressed=estop))
            self.laser.off()
            return tel

        tel.locked = True
        tel.track_id = locked.id
        tel.target_xyz = locked.kf.position

        # 3. find the detection nearest the locked track (for size + wingbeat)
        det = self._nearest_detection(locked.kf.position, pts, det_by_pt)
        wb = self.wingbeat.analyze(det.wingbeat) if det else None
        tel.wingbeat_hz = wb.peak_hz if wb else 0.0

        # 4. aim at the LEAD point (compensate one control period of latency)
        lead = locked.kf.predict_position(1.0 / self.cfg.loop_hz)
        tel.lead_xyz = lead
        tel.dac = self.galvo.aim_at(lead)

        # 5. safety evaluation
        s = SafetyInputs(
            key_on=key_on,
            enclosure_closed=enclosure_closed,
            estop_pressed=estop,
            backstop_present=backstop_present,
            target_in_range=self.stereo.in_working_volume(locked.kf.position),
            target_size_px=det.size_px if det else 1e9,
            wingbeat_ok=bool(wb and wb.is_insect),
        )
        decision = self.safety.evaluate(s)
        tel.permit_fire = decision.permit_fire
        tel.reasons = decision.reasons
        tel.fired = self.laser.fire(decision)
        return tel

    def _nearest_detection(self, target_xyz, pts, dets) -> StereoDetection | None:
        best, best_d = None, float("inf")
        for p, d in zip(pts, dets):
            dd = float(np.linalg.norm(p - target_xyz))
            if dd < best_d:
                best_d, best = dd, d
        return best


def run_headless(cfg: TurretConfig, n_steps: int = 300, seed: int = 0,
                 verbose: bool = False):
    """Fit calibration from the sim, then run the loop against a synthetic fly.

    Returns a summary dict (also the basis of the smoke test)."""
    from .sim import FlySimulator

    sim = FlySimulator(cfg, seed=seed)
    pts, dacs = sim.make_calibration_samples(500)
    calib = GalvoCalibration(cfg.galvo.calib_degree,
                             cfg.galvo.dac_min, cfg.galvo.dac_max).fit(pts, dacs)

    galvo = MockGalvo()
    laser = MockLaser()
    pipe = TurretPipeline(cfg, calib, galvo=galvo, laser=laser)

    dt = 1.0 / cfg.loop_hz
    aim_err_dac = []
    fired = 0
    locked_steps = 0
    for i in range(n_steps):
        true_pos, dets = sim.step(dt)
        tel = pipe.step(
            dets, dt,
            key_on=True, enclosure_closed=True, estop=False,
            backstop_present=True,
        )
        if tel.locked:
            locked_steps += 1
        if tel.dac is not None:
            # true DAC needed to hit the fly's ACTUAL current position
            true_dac = sim.truth_galvo.dac_for(true_pos)
            err = float(np.linalg.norm(np.array(tel.dac) - true_dac))
            aim_err_dac.append(err)
        if tel.fired:
            fired += 1
        if verbose and i % 30 == 0:
            print(f"[{i:4d}] locked={tel.locked} id={tel.track_id} "
                  f"wb={tel.wingbeat_hz:5.0f}Hz dac={tel.dac} "
                  f"fire={tel.fired} {tel.reasons}")

    # convert steady-state DAC error to an approximate angular error (rad)
    warm = aim_err_dac[cfg.tracker.min_hits_to_lock + 5:]
    mean_err_dac = float(np.mean(warm)) if warm else float("nan")
    approx_rad = mean_err_dac / sim.truth_galvo.kx  # kx = DAC per radian
    return {
        "calib_residual_rms_dac": calib.residual_rms,
        "locked_fraction": locked_steps / n_steps,
        "fired_steps": fired,
        "mean_aim_error_dac": mean_err_dac,
        "approx_aim_error_mrad": approx_rad * 1000.0,
    }
