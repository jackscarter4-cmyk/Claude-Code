# Fly / Gnat Locking Laser Turret — DIY Build & Design Document

**Senior-project style engineering spec. Goal: a benchtop turret that visually
detects small flying insects (flies, fruit flies, gnats), tracks one, and points
a steered laser at it.** This document deliberately avoids inventing electronics.
Every subsystem is a lift of an existing, documented design, wired together
("Frankenstein" style). Where a real product or published build already solved a
problem, we copy it and cite it.

> **Runnable code.** The control software that implements this design lives in
> [`turret/`](../turret/) and runs the full detection → tracking → aiming → safety
> loop **headless against a synthetic fly, with no hardware** (`python turret/run_sim.py`).
> 17 tests pass. See [`turret/README.md`](../turret/README.md).

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

Approximate 2026 street prices, USD, hobby-grade parts — exclude shipping & tax,
vary by supplier. Qty × unit is folded into the line cost.

| Subsystem | Part | Qty | Unit | Cost |
|---|---|---:|---:|---:|
| **Compute & sensing** | | | | |
| Compute | NVIDIA Jetson Orin Nano Super dev kit | 1 | $249 | $249 |
| Cameras | Raspberry Pi Camera v2 (IMX219), stereo pair | 2 | $25 | $50 |
| IR flood | 850 nm LED illuminator board | 1 | $13 | $13 |
| Wingbeat sensor | BPW34 photodiode + transimpedance op-amp front-end | 1 | $8 | $8 |
| **Beam steering** | | | | |
| Galvos | 30 Kpps ILDA galvo set (2 galvos + drivers + PSU) | 1 | $160 | $160 |
| DAC | Microchip MCP4922 (dual 12-bit SPI) | 1 | $4 | $4 |
| Level shift | dual op-amp gain board (0–5 V → ±12 V) | 1 | $6 | $6 |
| **Laser** | | | | |
| Bring-up laser | <1 mW Class 2 red pointer (develop on this) | 1 | $8 | $8 |
| Target laser | focusable 445 nm 5 W engraver diode + driver (added last) | 1 | $85 | $85 |
| **Safety & enclosure** | | | | |
| Goggles | OD5+ 445 nm laser goggles (per person) | 1 | $40 | $40 |
| E-stop | latching mushroom button (NC) | 1 | $10 | $10 |
| Key switch | keyed arming switch | 1 | $8 | $8 |
| Warning | laser-on indicator beacon | 1 | $10 | $10 |
| Enclosure | opaque enclosure + lid interlock switch | 1 | $45 | $45 |
| **Structure & wiring** | | | | |
| Frame | 2020 extrusion spans + 3D-printed PETG brackets (§4.5) | 1 | $35 | $35 |
| Wiring | logic PSU, connectors, misc | 1 | $25 | $25 |
| | | | **Total** | **$756** |

**Cheaper starting points**
- Skip the 445 nm diode and develop entirely on the <1 mW pointer: **−$85 → $671**
  (this is also the *recommended* first build — nothing here is an eye hazard).
- Reuse a Jetson you own, or run a Raspberry Pi 5 instead: **−$249 → $507**.

The 445 nm diode is the single line that turns this into a Class-4 eye hazard.
Build and tune the entire tracking chain first on the pointer (§6).

---

## 4.5 3D-printing the frame (physically verified)

**The governing requirement is stiffness and dimensional stability, not
strength.** The galvos steer the *beam*, not the head, so the frame never slews
and carries no dynamic load — just gravity (static) plus thermal and creep drift.
That single fact drives every choice below, and it means the honest answer is a
**hybrid frame: 3D-print the brackets, keep the long rigid spans in aluminum
extrusion.** Printing a monolithic frame would be the wrong call — FDM plastic is
an order of magnitude less stiff and far less thermally stable than 2020 extrusion,
and most hobby beds (220–256 mm) can't print a full frame in one piece anyway.

### Error budget the frame must respect
Steady-state tracking error is **~7.6 mrad** (§7 / `run_sim.py`). A galvo body
that tilts by angle θ swings the *reflected* beam by **2θ**, so mechanical drift is
doubly amplified. Budget: keep frame-induced beam drift **≤ 0.5 mrad** (≈7 % of the
error budget) → each galvo-carrying part must hold **≤ 0.25 mrad of angular drift**.
Note a *constant* deflection is harmless — it is absorbed when you calibrate in
place (§5.1). The enemy is *change*: thermal expansion, creep, and non-elastic
handling. The closed-loop visual servo (§5.1) mops up slow drift, but the frame
must not hand it a moving target.

### Constraint 1 — static stiffness (beam bending, worked)
Model a printed galvo bracket as a cantilever carrying the metal scanner block
(~0.4 kg ⇒ P ≈ 4 N). Tip slope `θ = P·L² / (2·E·I)`, `I = b·h³/12`.
Printed PETG effective modulus **E ≈ 1.6 GPa** (bulk 2.2 GPa knocked down ~30 % for
FDM). For an arm `L = 40 mm`, width `b = 40 mm`:

| Wall thickness `h` | `I` (m⁴) | tip slope θ | beam drift 2θ | tip sag δ |
|---|---|---|---|---|
| 8 mm | 1.71e-9 | **1.17 mrad** | 2.34 mrad | 31 µm |
| 12 mm | 5.76e-9 | **0.35 mrad** | 0.70 mrad | 9 µm |

Because `θ ∝ 1/h³`, thickness is the cheap lever. **Conclusion:** a thin printed
arm is marginal; either mount the galvo block **directly to the extrusion** (best)
or use a **short, thick (≥12 mm), fully-triangulated PETG pad loaded in
compression**, not a slender cantilever. Cameras and electronics are grams and
pass easily on printed mounts.

### Constraint 2 — thermal drift (CTE, worked)
Stereo depth is `Z = f·B/d`, so `dZ/Z = dB/B` — baseline drift maps straight to
depth error. Printed PETG CTE ≈ **68 µm/m·°C** ([MakeItFrom](https://www.makeitfrom.com/material-properties/Glycol-Modified-Polyethylene-Terephthalate-PETG-PET-G)).
A `B = 60 mm` baseline over a `ΔT = 10 °C` warm-up:
`ΔB = 68e-6 × 0.060 × 10 = 41 µm` → `dB/B = 0.068 %` → at 0.5 m that's **0.34 mm**
of depth error. Tolerable, but the *angular* toe-in drift of the two cameras is more
sensitive than the baseline length. **Conclusion:** carry the **camera baseline on
the aluminum member** (Al CTE ≈ 23 µm/m·°C, ~3× better) or a single short printed
part, and re-run the closed-loop calibration after warm-up.

### Constraint 3 — glass transition & creep (material choice)
Printed parts near the laser diode and galvo drivers see 40–60 °C. PLA's Tg is only
**60–65 °C** and it **creeps under sustained load well below Tg** — a precision mount
that must hold calibration for weeks will slowly walk. PETG (Tg **81 °C**) is the
minimum; **ASA (Tg 105 °C)** for anything touching the laser heatsink or a unit that
sees sunlight. **Do not use PLA for any optical or galvo mount.**

### Constraint 4 — FDM anisotropy & fasteners
FDM parts are weakest between layers (Z). Orient every bracket so bolt tension and
mount loads act **in-plane**, never pulling layers apart. **Use heat-set brass M3
inserts** for all repeated fastening (galvo, cameras, boards) — never self-tap the
plastic; tapped PLA/PETG strips after a few assembly cycles and destroys
repeatability. Add printed **datum bosses/slots** so a camera returns to the same
pose after removal (verify: re-seat 5×, calibration residual should stay within a
few DAC counts).

### Constraint 5 — keep laser heat off plastic
The 445 nm diode dumps several watts as heat and **must ride its own aluminum
heatsink**, thermally standoff-isolated from any printed part. Printed plastic is a
thermal insulator and will soften/creep if bolted straight to the diode housing.

### What to print vs. what stays metal
| Print in PETG/ASA | Keep metal |
|---|---|
| Camera stereo mounts (with datums) | 2020 aluminum extrusion — the rigid spans/baseline |
| Electronics tray (Jetson, MCP4922, op-amp) | Galvo scanner block (mount to extrusion or thick pad) |
| Photodiode + lens holder | Laser diode heatsink |
| Cable guides, backstop holder, enclosure panels | M3 heat-set inserts, fasteners |

### Verification procedure (measure, don't assume)
The math above is first-order; a graduate project *verifies it empirically*:
1. **Deflection test** — dial indicator on the galvo mount; hang 0.5 kg; confirm
   tip movement matches the table (tens of µm) **and fully recovers** (no permanent
   set). If it doesn't recover, thicken the pad or go metal.
2. **Thermal-soak / dot-drift test** — lock the beam on a fixed target, run galvos
   + laser 30 min, log the dot centroid in the camera. Drift must stay a small
   fraction of 7.6 mrad; if not, isolate heat or shorten the recalibration interval.
3. **Tap (resonance) test** — tap the frame, watch the dot ring-down on camera; the
   first natural frequency should sit well above ambient/scan vibration, and ring-
   down should damp quickly. Add mass/triangulation if it rings.
4. **Repeatability** — the re-seat test in Constraint 4.

Filament cost is negligible (~150–250 g PETG, a few dollars) and is folded into the
"extrusion frame + brackets" BOM line.

**Parametric CAD.** OpenSCAD for all four printed parts is in
[`turret/cad/`](../turret/cad/): the galvo pad and stereo camera mounts (the two
stiffness-critical brackets, sized from the constraints above), plus the
electronics tray (Jetson + MCP4922/op-amp) and the photodiode/lens holder for the
wing-beat sensor. The two brackets are laid out for a Bambu Lab P1S bed in
`print_plate.scad`; the tray and holder print from their own files.

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
