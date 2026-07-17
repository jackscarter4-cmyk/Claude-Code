#!/usr/bin/env python3
"""Run the whole turret loop headless against a synthetic fly — no hardware.

    python run_sim.py

Fits the galvo calibration from simulated DAC-sweep samples, then tracks a
synthetic hovering fly for a few seconds and reports lock rate, aiming error,
and how often the safety interlock permitted firing.
"""
from turret.config import TurretConfig
from turret.pipeline import run_headless


def main() -> None:
    cfg = TurretConfig()
    print("Fly-tracking laser turret — headless simulation")
    print("=" * 52)
    summary = run_headless(cfg, n_steps=360, seed=1, verbose=True)
    print("-" * 52)
    print(f"calibration fit residual : {summary['calib_residual_rms_dac']:.3f} DAC counts")
    print(f"locked fraction of frames: {summary['locked_fraction'] * 100:.1f}%")
    print(f"frames laser permitted   : {summary['fired_steps']}")
    print(f"mean aiming error        : {summary['mean_aim_error_dac']:.1f} DAC counts")
    print(f"          (~ angular)    : {summary['approx_aim_error_mrad']:.2f} mrad")
    print("=" * 52)


if __name__ == "__main__":
    main()
