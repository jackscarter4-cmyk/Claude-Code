# BUGLOCK printed brackets — parametric CAD (OpenSCAD)

Parametric CAD for the two brackets the frame actually needs, sized directly from
the verified constraints in [`docs/laser-fly-turret-design.md` §4.5](../../docs/laser-fly-turret-design.md).
Everything else (the rigid spans, the galvo block, the laser heatsink) stays
metal — see that section for why.

> Printer these are tuned for: **Bambu Lab P1S** — 256×256×256 bed (every part
> fits flat, foot/base down) and an **enclosed chamber**, which is what makes ASA
> practical here.
>
> ⚠️ These files were **not compile-checked in the build environment** (no
> OpenSCAD there). Open them in OpenSCAD or import to Bambu Studio and eyeball the
> geometry before committing filament.

## Files
| File | Part | Stiffness-critical? |
|---|---|---|
| `params.scad` | every dimension; each is annotated with the §4.5 constraint it serves | — |
| `galvo_pad.scad` | short solid pedestal for the galvo scanner block (Constraint 1) | **yes** |
| `camera_mount.scad` | slotted L-bracket holding one Pi Cam v2 (Constraint 2 & 4); **print two** | **yes** |
| `electronics_tray.scad` | standoff plate for the Jetson + MCP4922/op-amp board | no |
| `photodiode_holder.scad` | lens + BPW34 barrel on a slotted foot (wing-beat sensor, §3.2) | no |
| `print_plate.scad` | the two stiffness-critical brackets laid out for one P1S bed |  |

The tray and photodiode holder print from their **own files** (the tray is large;
the holder has a lens bore best sliced on its own) — open each and export its STL.

## MEASURE these before printing
The defaults are reasonable but your exact parts vary. Open `params.scad` and set:
- `galvo_block_w`, `galvo_block_d`, `galvo_bolt_dx`, `galvo_bolt_dy` — your galvo
  scanner block footprint and bolt pattern.
- `cam_pcb_w`, `cam_pcb_h`, `cam_hole_dx` — your camera board and its hole spacing.
- `tray_jetson_dx/dy`, `tray_aux_dx/dy` — the Jetson dev-kit and protoboard hole
  patterns (the Jetson values are nominal — confirm on your board).
- `pd_lens_od`, `pd_focal`, `pd_body_*` — your collecting lens and BPW34 body; set
  `pd_focal` to the lens's focal length so the photodiode lands in focus.

## Print settings (P1S)
| Setting | Galvo pad | Camera mount |
|---|---|---|
| Material | **ASA** (or PETG) — never PLA (§4.5 C3) | PETG or ASA |
| Layer height | 0.20 mm | 0.16–0.20 mm |
| Walls / perimeters | **≥ 5** | 4 |
| Infill | **≥ 50 %**, gyroid | 30–40 %, gyroid |
| Orientation | base down (as modelled) | foot down (as modelled) |
| Adhesion | brim (ASA) | brim (ASA) |

Non-critical parts (lighter settings are fine): **electronics tray** — 3 walls,
15–20 % infill, plate down; **photodiode holder** — 3 walls, 20 % infill, foot
down (the lens bore prints as an upward blind hole, no support). Neither holds an
optic in the aim path, so they don't need the bracket-grade stiffness.

Rationale: stiffness governs, not strength, so **more perimeters + higher infill**
buy stability where it matters. The galvo pad is the stiffness-critical part — do
not thin it or make it taller than `galvo_ped_h_max`.

## Fasteners
- **Galvo → pad:** M4 **heat-set brass inserts** in the top face (holes sized by
  `ins_m4_d`). Press in with a soldering iron.
- **Camera PCB → mount:** M2 heat-set inserts (`ins_m2_d`).
- **Boards → tray:** M3 heat-set inserts in the standoffs (`ins_m3_d`).
- **Lens → holder:** press-fit into the front seat; **photodiode:** friction pocket,
  a dab of hot-glue on the leads for strain relief.
- **Pad / mount → aluminum:** M5 bolts into **T-nuts** in the extrusion slot — no
  insert; that's why those are clearance holes with a top counterbore.

Never self-tap the plastic for anything you'll unscrew more than once — tapped
PETG/PLA strips and destroys the calibration-holding repeatability the datums give.

## Orientation note
Printed foot-down, the camera plate's M2 insert holes and the front window are
horizontal (bridged) features — fine after the heat-set insert seats, but inspect
the window bridge. If insert pull-out ever worries you, print the camera mount
plate-down instead (adds a little support) so those holes run along layers.

## After printing — verify (don't assume)
1. Set the stereo **baseline to 60 mm** with calipers by sliding each camera mount
   in its foot slot; lock the M5 bolts.
2. Run the §4.5 bench checks: **dial-indicator deflection** on the galvo pad, a
   **30-minute dot-drift soak**, and a **tap/resonance** test.
3. Re-seat each camera 5× and re-check the calibration residual (`run_sim.py`
   proves the software path; on hardware it should stay within a few DAC counts).
