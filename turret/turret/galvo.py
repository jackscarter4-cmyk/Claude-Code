"""Galvo mirror driver + the aim controller that sits on top of calibration.

Hardware chain (from Ildaron's Laser_control, unchanged):
    compute --SPI--> MCP4922 dual 12-bit DAC (0..4095) --> op-amp --> galvo driver

`MCP4922Galvo` is the real driver (needs `spidev` on a Pi/Jetson). `MockGalvo`
records writes so the whole pipeline runs and is testable with no hardware.
"""
from __future__ import annotations

from typing import Protocol

import numpy as np

from .calibration import GalvoCalibration
from .config import GalvoConfig


class GalvoDevice(Protocol):
    def write(self, dac_x: int, dac_y: int) -> None: ...


class MockGalvo:
    """In-memory galvo. Records every commanded DAC pair."""

    def __init__(self) -> None:
        self.history: list[tuple[int, int]] = []
        self.last: tuple[int, int] | None = None

    def write(self, dac_x: int, dac_y: int) -> None:
        self.last = (int(dac_x), int(dac_y))
        self.history.append(self.last)


class MCP4922Galvo:
    """Real MCP4922 driver over SPI. Imports spidev lazily so the package still
    imports on a dev machine without the library."""

    def __init__(self, cfg: GalvoConfig):
        import spidev  # type: ignore

        self.cfg = cfg
        self._spi = spidev.SpiDev()
        self._spi.open(cfg.spi_bus, cfg.spi_device)
        self._spi.max_speed_hz = 20_000_000
        self._spi.mode = 0

    def _frame(self, channel: int, value: int) -> list[int]:
        # MCP4922 12-bit write: [config nibble | 12-bit value]. Config bits:
        # bit15 A/B, bit14 BUF, bit13 /GA (1 = 1x gain), bit12 /SHDN (1 = active).
        value = max(0, min(4095, int(value)))
        cfg_bits = (channel & 1) << 15 | 0 << 14 | 1 << 13 | 1 << 12
        word = cfg_bits | (value & 0x0FFF)
        return [(word >> 8) & 0xFF, word & 0xFF]

    def write(self, dac_x: int, dac_y: int) -> None:
        self._spi.xfer2(self._frame(0, dac_x))
        self._spi.xfer2(self._frame(1, dac_y))


class GalvoController:
    """Aims the beam at a 3D point using the fitted calibration."""

    def __init__(self, device: GalvoDevice, calibration: GalvoCalibration):
        self.device = device
        self.calib = calibration
        self.last_dac: tuple[int, int] | None = None

    def aim_at(self, point_xyz: np.ndarray) -> tuple[int, int]:
        dac = self.calib.predict(point_xyz)
        self.device.write(*dac)
        self.last_dac = dac
        return dac
