# Fly / Gnat Locking Laser Turret — DIY Build & Design Document

**Senior-project style engineering spec. Goal: a benchtop turret that visually
detects small flying insects (flies, fruit flies, gnats), tracks one, and points
a steered laser at it.** This document deliberately avoids inventing electronics.
Every subsystem is a lift of an existing, documented design, wired together
("Frankenstein" style). Where a real product or published build already solved a
problem, we copy it and cite it.

> **Scope note.** This is a DIY design. It does **not** attempt the "neutralize
> without killing" energy problem — that is a genuinely hard, separately-studied
> question (thermal dose vs. wing damage vs. lethality) and is out of scope here.
> The laser subsystem is treated as a *pointable beam*; you choose the diode.
> **But laser eye-safety is not optional** and is treated as a first-class
> requirement below, because even a "DIY" tracking laser is an eye hazard the
> moment it exceeds ~1 mW.

---

## 1. Prior art we are copying (don't reinvent)

| System | What we steal from it | Reference |
|---|---|---|
| **Photonic Fence** (Intellectual Ventures / Global Good) | The overall architecture: wide-area IR illumination + retroreflector backstop, camera detection, insect ID by **wing-beat frequency**, galvo-steered targeting laser, and a *fire-only-with-a-solid-backstop* safety interlock. | Myhrvold et al., US Patents [US8705017B2](https://patents.google.com/patent/US8705017B2/en), [US10311293B2](https://patents.google.com/patent/US10311293B2/en); [optics.org coverage](https://optics.org/news/7/5/37) |
| **Ildaron `Laser_control`** (open-source) | The concrete DIY realization: RPi→Jetson Nano compute, Haar→**YOLOv4-tiny** detection, **stereo Pi cameras** for depth, **MCP4922 DAC over SPI → op-amp → galvo** aiming chain. This is our primary reference build — real, working, part numbers included. | [github.com/Ildaron/Laser_control](https://github.com/Ildaron/Laser_control); [Medium writeup](https://medium.com/nerd-for-tech/raspberry-pi-for-kill-mosquitoes-by-laser-e99334a97d68) |
| **Photon Matrix** (2025 portable unit) | Proof that a compact LiDAR + galvo unit works at consumer scale; validates ranging-before-firing. | [Wikipedia: Mosquito laser](https://en.wikipedia.org/wiki/Mosquito_laser) |
| **Optical wingbeat classification** literature | How to tell "small flying insect" from "dust / reflection / your hand" using wing-beat frequency, so the turret locks onto the right thing. | Chen et al., [*Flying Insect Detection and Classification with Inexpensive Sensors*](https://pmc.ncbi.nlm.nih.gov/articles/PMC4541473/); [Frontiers, fruitfly wingbeat CNN](https://www.frontiersin.org/journals/plant-science/articles/10.3389/fpls.2022.812506/full); [Sci. Reports, IR wingbeat sensor](https://www.nature.com/articles/s41598-021-89644-z) |
| **Keller et al.** — optical tracking & laser engagement of insects in flight | Peer-reviewed confirmation that camera-track + steered-laser on flying insects is real, with achievable tracking rates. | [*Optical tracking and laser-induced mortality of insects during flight*](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7481216/) |

**The thesis in one sentence:** *Photonic Fence architecture, shrunk to a
benchtop and rebuilt from Ildaron's exact bill of materials.*

---

## 2. What we're actually targeting (design drivers)

The target biology sets every hardware spec. From the wingbeat literature:

| Insect | Wingbeat freq | Body size | Flight |
|---|---|---|---|
| House fly (*Musca*) | ~150–200 Hz | 6–7 mm | fast, erratic |
| Fruit fly (*Drosophila*) | ~200–250 Hz | 2–3 mm | hovering, slow |
| Gnat / fungus gnat | ~300–600 Hz | 1–3 mm | weak, drifting |
| Mosquito (reference) | ~300–600 Hz (up to ~1000) | 3–6 mm | slow, ~40° wing amplitude |

Design consequences:
- **Small + close.** A 2 mm gnat is only resolvable and hittable at short range.
  Ildaron's working range was **~0.3 m (≈1 foot)**. Treat **0.3–1.0 m** as the
  design envelope. This is a benchtop / single-room-corner device, *not* a
  30 m Photonic Fence.
- **Frame rate beats resolution.** Erratic flies need a fast loop, not a 4K
  image. Ildaron hit **30–35 FPS** with YOLOv4-tiny + tkDNN on a Jetson Nano —
  adopt that as the target.
- **Wingbeat = the discriminator.** Size alone confuses dust, reflections, and
  fingers with insects. The Photonic Fence and the wingbeat papers both key on
  wing-beat frequency for identity. We use it as a *confirmation gate* before lock.

---

## 3. System architecture

```
                 ┌──────────────────────────────────────────────┐
                 │  Detection volume (0.3–1.0 m), matte backstop  │
                 │        + diffuse IR flood for contrast          │
                 └──────────────────────────────────────────────┘
      Stereo cameras  ─┐                                   ▲ laser dot
      (IMX219 x2)      │                                   │
                       ▼                                   │
              ┌─────────────────┐   XYZ target   ┌──────────────────┐
              │  Compute (Jetson │──────────────▶│  Aim solver +     │
              │  Orin Nano)      │  + wingbeat    │  galvo calibration│
              │  YOLO + stereo   │    gate        └──────────────────┘
              └─────────────────┘                         │ (x,y) angles
                       ▲                                   ▼
                 wingbeat FFT                     ┌──────────────────┐
                 from photodiode                  │ MCP4922 DAC (SPI) │
                       │                          │  → op-amp ±12V    │
              ┌─────────────────┐                 │  → galvo driver   │
              │ IR photodiode +  │                └──────────────────┘
              │ transimpedance amp│                        │
              └─────────────────┘                          ▼
                                                  ┌──────────────────┐
                                                  │  2× galvo mirrors │
                                                  │  X/Y + laser diode │
                                                  └──────────────────┘
                                                           │
                                          ┌────────────────┴───────────────┐
                                          │  SAFETY INTERLOCK CHAIN          │
                                          │  backstop-present · human-detect │
                                          │  · key switch · e-stop · enclosure│
                                          └──────────────────────────────────┘
```

Five subsystems, each mapped to a proven source:

### 3.1 Sensing — detect & locate the insect
- **2× Raspberry Pi Camera v2 (Sony IMX219)** in a fixed stereo baseline (Ildaron's
  choice). Global-shutter is *better* for fast insects but IMX219 (rolling) is the
  proven-cheap path; start there.
- **Diffuse near-IR flood (850 nm) + matte dark backstop.** Copied from Photonic
  Fence's IR-illumination-against-a-known-background trick: insects show up as
  bright moving blobs, and — critically — the backstop *is* the safety condition
  for firing (§6).
- Optional but recommended: **retroreflective tape** on the backstop (Photonic
  Fence uses retroreflectors) to maximize insect-vs-background contrast.

### 3.2 Perception — is it a bug, and where?
- **Detector:** YOLOv4-tiny (Ildaron) or a modern small model (YOLOv8n / YOLO11n)
  for x,y in the image. Train on your own captured fly/gnat clips — the
  [Insect Detect](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10990185/) DIY
  camera-trap project is a ready-made dataset/pipeline reference.
- **Depth (Z):** stereo disparity from the two IMX219s (Ildaron's method). Depth is
  what lets you both focus and range-gate for safety.
- **Wingbeat confirmation gate:** an **IR photodiode + transimpedance amplifier**
  looks at the candidate; an FFT of its ~1 kHz-sampled signal must show a peak in
  the 100–700 Hz insect band before the turret will *lock*. This is the single
  biggest false-positive killer and is straight from the wingbeat papers and the
  Photonic Fence patents (identify by wingbeat harmonics).

### 3.3 Aiming — point the beam
This is copied verbatim from Ildaron's electronics chain — do not redesign it:
```
Jetson  --SPI-->  MCP4922 dual DAC (0–5 V)  -->  op-amp gain stage (→ ±12 V)
        -->  galvanometer driver board  -->  2× closed-loop galvo mirrors (X,Y)
```
- **Galvo set:** any hobby "20 Kpps ILDA" laser-show galvo kit (includes mirrors,
  drivers, and ±12–15 V supply). These are mass-produced and cheap.
- **DAC:** Microchip **MCP4922** (12-bit, dual, SPI) — exactly Ildaron's part.
- Calibration ranges in Ildaron's build: X mirror steps 350–550, Y 0–250 — yours
  will differ; you *measure* them (§5).

### 3.4 Emission — the laser
- Treated as a pointable beam module. For a DIY build people repurpose **laser-engraver
  diode modules** (445 nm blue, 1–5 W, from a K40/engraver spares bin) because
  they're collimated, focusable, and cheap.
- **⚠ The moment you exceed ~1 mW you have an eye hazard.** See §6. **Bring up and
  tune the entire tracking system with a <1 mW Class 2 red pointer first.** Only
  swap in a real diode after the safety chain is built, inside an enclosure, wearing
  wavelength-matched goggles. Ildaron's repo says the same thing bluntly: *"Don't
  use the power laser!"* during development.

### 3.5 Compute
- **NVIDIA Jetson Orin Nano** (modern replacement for Ildaron's Jetson Nano). Runs
  the detector, stereo, aim solver, and safety logic. A Raspberry Pi 5 + Hailo
  accelerator is a viable alternative but the Jetson path is the documented one.

---

## 4. Bill of materials (starter, ~short-range benchtop)

| Subsystem | Part | Notes / source |
|---|---|---|
| Compute | NVIDIA Jetson Orin Nano dev kit | runs YOLO @ 30+ FPS |
| Cameras | 2× Raspberry Pi Camera v2 (IMX219) | stereo pair, Ildaron's choice |
| IR flood | 850 nm LED illuminator | contrast against backstop |
| Wingbeat sensor | IR photodiode (e.g. BPW34) + transimpedance op-amp | 100–700 Hz gate |
| Beam steering | 20 Kpps ILDA galvo kit (2 mirrors + drivers + ±12 V PSU) | mass-market |
| DAC | Microchip MCP4922 (dual 12-bit SPI) | Ildaron's exact part |
| Level shift | dual op-amp gain board (0–5 V → ±10–12 V) | Ildaron's chain |
| **Bring-up laser** | **<1 mW Class 2 red pointer** | **use this the whole time you develop** |
| Target laser (later) | focusable 445 nm engraver diode module | only inside enclosure + interlocks |
| Safety | key switch, mushroom E-stop, indicator beacon, enclosure, OD-rated goggles | §6, non-negotiable |
| Structure | rigid aluminum extrusion frame | camera+galvo rigidity = aim accuracy |

---

## 5. The two hard problems (and the proven fixes)

### 5.1 Camera-to-galvo calibration (pixel → mirror angle)
The whole thing lives or dies on mapping an image pixel `(u,v)` at depth `Z` to
galvo DAC codes `(dx, dy)`. Proven approach (Ildaron + standard laser-show practice):
1. Command a grid of DAC codes; for each, detect where the *bring-up* laser dot
   lands in the camera image. This builds a lookup of `(dx,dy) → (u,v)` at a known
   depth plane.
2. Fit a low-order polynomial / homography to invert it: `(u,v,Z) → (dx,dy)`.
3. Because targets move in Z, capture the map at 2–3 depth planes and interpolate.
4. **Closed-loop correction:** after commanding an aim, the camera *sees* the dot;
   drive residual error to zero (visual servoing). This makes the polynomial only
   need to be "close," and absorbs drift — much more robust than open-loop.

### 5.2 Tracking a fast, erratic target
- **Predict, don't chase.** Run a constant-velocity **Kalman filter** per track;
  aim at the *predicted* position one loop ahead to cancel the ~30–50 ms
  sense→aim latency. Keller et al. and every working build rely on prediction.
- **Lock one target.** Nearest-neighbor data association; don't thrash between bugs.
- **Fps > everything.** Keep the detector tiny (YOLO-*n*/tiny) so the loop stays
  ≥30 FPS. A slow accurate detector loses the fly; a fast rough one plus Kalman wins.

---

## 6. Safety (mandatory — a graded requirement, not an afterthought)

A tracking laser is a **moving beam of unpredictable direction**. That is exactly
the case laser-safety standards worry about. This section is copied from real
practice (IEC 60825, ILDA laser-show safety, Photonic Fence interlocks) — see
[IEC 60825 overview](https://en.wikipedia.org/wiki/Laser_safety) and the
[ILDA hardware safety list](https://www.ilda.com/resources/ILDA-safety7b.pdf).

1. **Class the system honestly.** >1 mW visible = Class 3R+, an eye hazard. A
   focusable 445 nm engraver diode is **Class 4**. Class 4 legally/► practically
   requires: key switch, emission indicator, interlocked enclosure, e-stop,
   controlled access, and OD-rated goggles matched to the wavelength.
2. **Fire only with a solid, in-range backstop.** Directly from Photonic Fence: if
   the stereo system does not see a known matte backstop *behind* the target within
   the calibrated range, the laser is inhibited. No backstop → no fire. This stops
   the beam from ever leaving the box toward a face or window.
3. **Range gate.** Only permit firing when target depth Z is inside the calibrated
   near/far planes (the Photon Matrix "disable if beyond effective range" rule).
4. **Human/large-object cutout.** The Photon Matrix and Photonic Fence both refuse
   targets that are too big or the wrong wing-cadence. Reuse the YOLO detector +
   wingbeat gate: any object above a size threshold, or lacking an insect-band
   wingbeat, hard-inhibits firing.
5. **Interlock chain in hardware, not just software.** Backstop-present AND
   enclosure-closed AND key-on AND not-e-stopped must be a physical AND that gates
   laser power. Software can *only add* inhibits, never override them.
6. **Develop at <1 mW.** Build, calibrate, and demo the entire tracking pipeline
   with a Class 2 pointer. The high-power diode is the *last* thing added.
7. **Never point at reflective surfaces / people / pets. Enclose the working
   volume.** Windows, mirrors, glossy tables create stray beams.

> If this is a graded project: a laser-safety analysis (class, MPE, NOHD, interlock
> FMEA) is itself a deliverable and demonstrates rigor. Do it.

---

## 7. Build & validation milestones (project plan)

| Phase | Deliverable | Success test |
|---|---|---|
| 0 | Safety analysis + enclosure + interlock chain | e-stop and open-lid both kill laser power in HW |
| 1 | Stereo rig + IR flood + backstop | live depth map; insect blobs visible |
| 2 | YOLO fly/gnat detector | ≥30 FPS, detects real fruit flies on video |
| 3 | Wingbeat photodiode gate | FFT peak 100–700 Hz distinguishes bug from waving hand |
| 4 | Galvo + MCP4922 chain + calibration | bring-up dot lands within a few mm of commanded pixel |
| 5 | Closed-loop visual servo + Kalman track | red dot *follows* a walking, then flying, fruit fly |
| 6 | (Optional) high-power diode, fully interlocked | supervised, goggles, enclosed only |

Phases 0–5 are the honest core of the project and are all achievable with a
harmless pointer. Phase 6 is where it becomes dangerous and is optional.

---

## 8. Honest limitations (state these up front)

- **Range is tiny.** Expect ~0.3–1 m. Small insects are simply not resolvable or
  hittable far away — this is physics, not a tuning problem. Ildaron got ~1 foot.
- **Throughput is low.** Documented DIY builds manage ~2 targets/second at best.
- **Focus at range is finicky** for a collimated diode; the target must be near the
  focal plane. Depth-driven focus is an advanced add-on.
- **Rolling-shutter + fast wings** smears imagery; a global-shutter camera is the
  first upgrade if detection struggles.
- **This is a supervised benchtop instrument, not an unattended appliance.**

---

## References

1. Myhrvold et al. *Photonic Fence.* US Patents [US8705017B2](https://patents.google.com/patent/US8705017B2/en), [US10311293B2](https://patents.google.com/patent/US10311293B2/en), [US20100186284A1](https://patents.google.com/patent/US20100186284A1/en).
2. optics.org. *Photonic fence for pest control 'now practical'.* https://optics.org/news/7/5/37
3. Ildaron. *Laser_control — Laser for control mosquito, weed, and pest.* https://github.com/Ildaron/Laser_control ; Medium: https://medium.com/nerd-for-tech/raspberry-pi-for-kill-mosquitoes-by-laser-e99334a97d68
4. Chen, Why, Batista et al. *Flying Insect Detection and Classification with Inexpensive Sensors.* PMC4541473. https://pmc.ncbi.nlm.nih.gov/articles/PMC4541473/
5. *Optical Identification of Fruitfly Species Based on Their Wingbeats Using CNNs.* Frontiers in Plant Science. https://www.frontiersin.org/journals/plant-science/articles/10.3389/fpls.2022.812506/full
6. *Infrared light sensors permit rapid recording of wingbeat frequency and bioacoustic species identification of mosquitoes.* Scientific Reports. https://www.nature.com/articles/s41598-021-89644-z
7. Keller et al. *Optical tracking and laser-induced mortality of insects during flight.* PMC7481216. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7481216/
8. Sittinger et al. *Insect Detect: open-source DIY camera trap for automated insect monitoring.* PMC10990185. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10990185/
9. IEC 60825-1 laser safety (overview). https://en.wikipedia.org/wiki/Laser_safety ; ILDA hardware safety list. https://www.ilda.com/resources/ILDA-safety7b.pdf
10. *Mosquito laser* (Photon Matrix, portable unit). https://en.wikipedia.org/wiki/Mosquito_laser
