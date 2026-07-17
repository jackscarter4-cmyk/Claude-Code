import numpy as np

from turret.config import WingbeatConfig
from turret.wingbeat import WingbeatGate


def _tone(freq, n=512, fs=2000.0, noise=0.2, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(n) / fs
    return np.sin(2 * np.pi * freq * t) + noise * rng.standard_normal(n)


def test_insect_wingbeat_passes():
    gate = WingbeatGate(WingbeatConfig())
    res = gate.analyze(_tone(220))          # fruit-fly-ish
    assert res.is_insect
    assert abs(res.peak_hz - 220) < 15


def test_hand_wave_rejected():
    gate = WingbeatGate(WingbeatConfig())
    res = gate.analyze(_tone(8, noise=0.05))  # slow motion, out of band
    assert not res.is_insect


def test_pure_noise_rejected():
    gate = WingbeatGate(WingbeatConfig())
    rng = np.random.default_rng(1)
    res = gate.analyze(rng.standard_normal(512))
    assert not res.is_insect


def test_short_signal_is_rejected_gracefully():
    gate = WingbeatGate(WingbeatConfig())
    res = gate.analyze(np.zeros(4))
    assert not res.is_insect
