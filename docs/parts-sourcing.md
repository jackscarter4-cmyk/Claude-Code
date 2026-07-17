# BUGLOCK — parts sourcing sheet (Temu-first)

Type the **search term** into Temu, match the **spec**, done. A few parts aren't
really sold on Temu — those rows say where to get them instead. Prices are rough
2026 street prices, USD.

## Compute & sensing
| # | Part | Temu search term | Match this spec | Qty | ~$ |
|---|---|---|---|---|---|
| 1 | Main compute | — *(not on Temu)* | NVIDIA Jetson Orin Nano Super dev kit — Amazon/Seeed/Arrow | 1 | 249 |
| 2 | Cameras | `IMX219 camera module 8MP` | IMX219 sensor, CSI ribbon, ~25×24 mm board | 2 | 25 ea |
| 3 | IR flood | `850nm IR illuminator board CCTV` | 850 nm, 12 V, 36–48 LED board | 1 | 13 |
| 4 | Wing-beat sensor | `BPW34 photodiode` + `TL072 op amp` | BPW34 PIN diode; dual op-amp for the transimpedance amp | 1 | 8 |

## Beam steering
| # | Part | Temu search term | Match this spec | Qty | ~$ |
|---|---|---|---|---|---|
| 5 | Galvos | `laser galvo scanner 30K ILDA` *(else AliExpress)* | 30 Kpps closed-loop set: 2 galvos + drivers + PSU | 1 | 160 |
| 6 | DAC | `MCP4922 DAC module` *(else AliExpress)* | MCP4922, dual 12-bit, SPI | 1 | 4 |
| 7 | Level shift | `dual op amp board` | 0–5 V → ±12 V op-amp stage (or build on protoboard) | 1 | 6 |

## Laser
| # | Part | Temu search term | Match this spec | Qty | ~$ |
|---|---|---|---|---|---|
| 8 | Bring-up laser | `red laser pointer 1mW` | **<1 mW, Class 2**, red — develop on this | 1 | 8 |
| 9 | Target laser | `445nm 5W laser engraver module` | Focusable 445 nm, ~5 W, 12 V, TTL/PWM, w/ driver | 1 | 85 |

## Safety & enclosure
| # | Part | Temu search term | Match this spec | Qty | ~$ |
|---|---|---|---|---|---|
| 10 | Goggles | `445nm laser safety goggles OD5` | **OD5+ at 445 nm** (one pair per person) | 1 | 40 |
| 11 | E-stop | `emergency stop button latching NC` | 22 mm mushroom, latching, normally-closed | 1 | 10 |
| 12 | Key switch | `key switch 2 position` | 2-position keyed, panel mount | 1 | 8 |
| 13 | Warning light | `12V LED warning light` | 12 V indicator beacon (laser-on) | 1 | 10 |
| 14 | Enclosure | `project enclosure box` + `door limit switch` | Opaque box big enough for the volume + a lid switch | 1 | 45 |

## Structure, print & wiring
| # | Part | Temu search term | Match this spec | Qty | ~$ |
|---|---|---|---|---|---|
| 15 | Frame | `2020 aluminum extrusion kit` + `M5 T nuts` | 2020 profile + brackets + T-nuts + M5 bolts | 1 | 35 |
| 16 | Heat-set inserts | `heat set inserts M2 M3 M4 brass` | Brass melt-in inserts (M2 cams, M3 tray, M4 galvo) | 1 | 8 |
| 17 | Lens (photodiode) | `12mm optical lens` | ~12 mm dia collecting lens for the sensor barrel | 1 | 4 |
| 18 | Filament | `ASA filament 1kg` *(or `PETG filament 1kg`)* | ASA or PETG — **not PLA** | 1 | 22 |
| 19 | Power + wiring | `12V 5A power supply` + `dupont jumper wires` | 12 V PSU for galvos/logic, hookup wire, connectors | 1 | 25 |

---

**Rough total ≈ $780** (incl. filament + inserts). Skip the 445 nm diode to start
safe: **− $85**. Already have a Jetson: **− $249**.

**Not on Temu — buy elsewhere:** Jetson (#1, Amazon/Seeed). The galvo set (#5) and
bare MCP4922 (#6) are hit-or-miss on Temu; **AliExpress** is the reliable source.

**Watch the spec, not the photo:** for the galvos insist on *closed-loop 30 Kpps
with drivers + PSU included*; for the goggles insist on the **OD5+ @ 445 nm**
number printed on the lens — cheap "laser glasses" often aren't rated.
