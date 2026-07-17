"""Fly/gnat-tracking laser turret — control software.

See docs/laser-fly-turret-design.md for the architecture and prior art this
implements (Photonic Fence + Ildaron Laser_control + optical wingbeat ID).
"""
from .config import TurretConfig
from .pipeline import TurretPipeline, run_headless

__all__ = ["TurretConfig", "TurretPipeline", "run_headless"]
