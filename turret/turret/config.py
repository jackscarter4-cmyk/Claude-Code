"""Central configuration for the fly-tracking laser turret.

Every tunable lives here as a dataclass so the whole system is configured from
one object and nothing is hidden in magic numbers. Values are the benchtop
short-range defaults from the design doc (docs/laser-fly-turret-design.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StereoConfig:
    """Pinhole stereo model. Two identical cameras, horizontal baseline."""

    fx: float = 1400.0          # focal length in pixels
    fy: float = 1400.0
    cx: float = 640.0           # principal point (image is 1280x720)
    cy: float = 360.0
    baseline_m: float = 0.06    # 6 cm between the two IMX219 cameras
    image_w: int = 1280
    image_h: int = 720

    # Working volume the turret is calibrated and safe to fire within (metres,
    # camera frame: +X right, +Y down, +Z forward/away from the turret).
    z_min_m: float = 0.30
    z_max_m: float = 1.00


@dataclass
class WingbeatConfig:
    """Photodiode wing-beat gate."""

    sample_rate_hz: float = 2000.0   # >2x the max wingbeat we care about
    band_lo_hz: float = 100.0        # insect wingbeat band (flies/gnats/mosquitoes)
    band_hi_hz: float = 700.0
    min_power_ratio: float = 4.0     # in-band peak must be this x the median bin
                                     # (>3 to clear the white-noise floor of a
                                     # single window; real gate integrates several)


@dataclass
class TrackerConfig:
    """Constant-velocity Kalman tracker + data association."""

    process_pos_var: float = 5e-4    # q on position (m^2)
    process_vel_var: float = 0.1     # q on velocity ((m/s)^2) — low enough for a
                                     # smooth velocity estimate, high enough to
                                     # follow a hovering insect's slow turns
    meas_var_m2: float = 1e-4        # r, measurement noise (m^2)
    gate_m: float = 0.08             # association gate radius (metres)
    max_misses: int = 8              # drop a track after this many missed frames
    min_hits_to_lock: int = 3        # confirm a track before locking on to it


@dataclass
class GalvoConfig:
    """DAC / galvo limits. MCP4922 is a 12-bit dual DAC (0..4095)."""

    dac_min: int = 0
    dac_max: int = 4095
    calib_degree: int = 3            # polynomial degree for pixel/3D -> DAC map
                                     # (deg 3 captures the galvo's atan nonlinearity)
    spi_bus: int = 0
    spi_device: int = 0


@dataclass
class SafetyConfig:
    """Interlock thresholds. Firing is inhibited unless *all* pass."""

    max_target_px: float = 60.0      # bbox bigger than this => not an insect => inhibit
    require_backstop: bool = True
    require_enclosure: bool = True
    require_key: bool = True


@dataclass
class TurretConfig:
    stereo: StereoConfig = field(default_factory=StereoConfig)
    wingbeat: WingbeatConfig = field(default_factory=WingbeatConfig)
    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    galvo: GalvoConfig = field(default_factory=GalvoConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)

    loop_hz: float = 60.0            # target control-loop rate
