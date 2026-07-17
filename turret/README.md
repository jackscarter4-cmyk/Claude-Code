# Fly/Gnat Laser Turret — control software

Runnable control code for the turret designed in
[`docs/laser-fly-turret-design.md`](../docs/laser-fly-turret-design.md). It's the
software half of the "Frankenstein of proven tech" build: Photonic Fence
architecture + Ildaron's `Laser_control` electronics chain + optical-wingbeat ID.

**The whole thing runs headless with no hardware** — a synthetic-fly simulator
drives the real detection→tracking→aiming→safety pipeline, so you can develop and
test the logic on a laptop and only swap in real drivers on the Jetson/Pi.

## Quick start

```bash
pip install -r requirements.txt        # numpy + pytest
python run_sim.py                      # run the full loop against a synthetic fly
PYTHONPATH=. python -m pytest -q       # 17 unit/integration tests
```

`run_sim.py` output (fits the galvo calibration, then tracks a hovering fly):

```
calibration fit residual : ~1.6 DAC counts
locked fraction of frames: ~99%
mean aiming error        : ~7-8 mrad   (a few mm at 0.5 m)
```

## What's real code vs. what's a stub

| Module | Status |
|---|---|
| `geometry.py` — stereo triangulation | real, tested |
| `calibration.py` — 3D→DAC polynomial map (fit + predict) | real, tested |
| `tracking.py` — constant-velocity Kalman + single-target lock | real, tested |
| `wingbeat.py` — FFT wing-beat gate | real, tested |
| `safety.py` — interlock state machine (latching e-stop) | real, tested |
| `pipeline.py` — the control loop wiring it together | real, tested |
| `sim.py` — synthetic fly + true galvo + synthetic sensors | real, drives tests |
| `galvo.py` — `MockGalvo` (dev) / `MCP4922Galvo` (SPI, real hw) | mock tested; hw path lazy-imports `spidev` |
| `laser.py` — `MockLaser` (dev) / `GpioLaser` (real hw) | mock tested; hw path lazy-imports `RPi.GPIO` |
| `detector.py` — `YoloStereoDetector` | skeleton; wire up your model + cameras |

The mock and hardware classes share the same interfaces and the same safety
logic, so moving to hardware is: implement `detect()` with your camera+YOLO,
construct `MCP4922Galvo`/`GpioLaser` instead of the mocks, and feed the real
interlock booleans into `pipeline.step()`.

## Bringing it to hardware (order matters — see design doc §6)

1. Build the physical safety chain first (key, enclosure switch, e-stop) so it
   cuts laser power in hardware. `safety.py` is the software half only.
2. Collect real calibration samples: command a DAC grid with a **<1 mW bring-up
   laser**, detect the dot, feed `(point_xyz, dac_xy)` to `GalvoCalibration.fit`.
3. Bring up detection + tracking + aiming entirely with the bring-up laser.
4. Only then, inside a closed interlocked enclosure with goggles, swap in the
   real diode.

## Safety

This code refuses to fire unless the interlock permits (backstop present, target
in range and insect-sized, wing-beat confirmed, key on, enclosure closed, no
e-stop). That is defence-in-depth on top of — never instead of — a hardware
interlock that physically cuts laser power. Do not run a >1 mW laser outside an
enclosure.
