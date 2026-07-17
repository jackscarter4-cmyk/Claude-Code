"""Wing-beat confirmation gate.

Size and motion alone confuse dust, glints, and a waving hand with insects. The
discriminator every serious system uses (Photonic Fence, the optical-wingbeat
papers) is the wing-beat frequency: a real fly/gnat shows a strong periodic
component in the ~100-700 Hz band on a photodiode. This gate confirms that
before the tracker is allowed to lock and fire.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import WingbeatConfig


@dataclass
class WingbeatResult:
    is_insect: bool
    peak_hz: float
    power_ratio: float


class WingbeatGate:
    def __init__(self, cfg: WingbeatConfig):
        self.cfg = cfg

    def analyze(self, signal: np.ndarray) -> WingbeatResult:
        c = self.cfg
        x = np.asarray(signal, dtype=float)
        if x.size < 16:
            return WingbeatResult(False, 0.0, 0.0)
        x = x - x.mean()
        # windowed magnitude spectrum
        win = np.hanning(x.size)
        spec = np.abs(np.fft.rfft(x * win))
        freqs = np.fft.rfftfreq(x.size, d=1.0 / c.sample_rate_hz)

        band = (freqs >= c.band_lo_hz) & (freqs <= c.band_hi_hz)
        if not np.any(band):
            return WingbeatResult(False, 0.0, 0.0)

        band_spec = spec[band]
        band_freqs = freqs[band]
        peak_i = int(np.argmax(band_spec))
        peak_hz = float(band_freqs[peak_i])
        peak_val = float(band_spec[peak_i])

        # Reference floor = median of the *whole* spectrum (excluding DC), so a
        # low-frequency hand-wave (energy outside the band) does not pass.
        floor = float(np.median(spec[1:])) + 1e-9
        ratio = peak_val / floor
        is_insect = ratio >= c.min_power_ratio
        return WingbeatResult(is_insect, peak_hz, ratio)
