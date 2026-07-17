// =============================================================================
//  BUGLOCK printed-bracket parameters
//  Every structural number here traces to docs/laser-fly-turret-design.md §4.5.
//  Units: millimetres. Target printer: Bambu Lab P1S (256^3 bed, enclosed).
//  Material: PETG minimum, ASA preferred near the laser.  NEVER PLA (§4.5 C3).
// =============================================================================

$fn = 64;                 // curve smoothness for render/export

// ---- general print process (P1S, 0.4 mm nozzle) --------------------------
wall        = 3.2;        // ~8 perimeters — stiffness governs, not strength
gen_clr     = 0.20;       // clearance between mating printed features

// ---- 2020 / 2040 aluminum extrusion interface ----------------------------
ext_w       = 20;         // 2020 profile face width (rail top the foot sits on)
m5_clear    = 5.5;        // clearance hole for an M5 bolt into a T-nut
m5_head     = 9.6;        // M5 socket-head cap dia (counterbore)
m5_head_h   = 5.0;        // M5 head height (counterbore depth)

// ---- heat-set brass inserts (melt-in hole diameters) ---------------------
// Verify against YOUR inserts; these are typical for CNC Kitchen / generic.
ins_m2_d    = 3.2;  ins_m2_h = 4.0;
ins_m3_d    = 4.0;  ins_m3_h = 6.0;
ins_m4_d    = 5.7;  ins_m4_h = 8.0;

// =============================================================================
//  GALVO PAD  — §4.5 Constraint 1 (static stiffness)
//  Cantilever calc: slope theta = P*L^2/(2*E*I), I = b*h^3/12, so theta ~ 1/h^3.
//  Under the ~4 N scanner block a >=12 mm pad flexes ~0.35 mrad (vs 1.17 mrad at
//  8 mm) — inside the 0.5 mrad frame budget. The pad is a SHORT SOLID pedestal
//  (load in compression, short moment arm), not a slender arm.
// =============================================================================
galvo_ped_h      = 16;    // pedestal height — keep SHORT (short moment arm)
galvo_ped_h_max  = 20;    // do not exceed: taller = longer lever = more drift
galvo_pad_t_min  = 12;    // informational: min effective load-path thickness

// Footprint of YOUR galvo scanner block — MEASURE and edit these two:
galvo_block_w    = 66;    // block width  (X)
galvo_block_d    = 40;    // block depth  (Y)

// Galvo block bolt pattern — MEASURE your kit (most 20-30 kpps sets are M4):
galvo_bolt_dx    = 50;    // bolt spacing across X
galvo_bolt_dy    = 28;    // bolt spacing across Y
galvo_bolt_ins   = ins_m4_d;
galvo_bolt_ins_h = ins_m4_h;

galvo_margin     = 11;    // pad border beyond the block (room for hold-downs)
galvo_hold_edge  = 7;     // hold-down bolt inset from the pad edge
cable_slot_w     = 12;    // cable relief channel width
cable_slot_h     = 5;     // cable relief channel height (from base up)

// =============================================================================
//  CAMERA MOUNT — §4.5 Constraint 2 & 4
//  Baseline (60 mm) is carried on the ALUMINUM rail, not plastic: the foot has a
//  SLOT so you slide each camera to an exact baseline, then lock it. Print two.
//  PCB is captured in a lipped pocket (a datum) so it re-seats repeatably.
// =============================================================================
// Raspberry Pi Camera v2 (IMX219) board — MEASURE yours; boards vary slightly:
cam_pcb_w        = 25.0;
cam_pcb_h        = 24.0;
cam_pcb_t        = 1.6;
cam_pocket_clr   = 0.35;  // pocket oversize so the PCB drops in cleanly

cam_lip_w        = 3.0;   // pocket wall/lip width around the PCB
cam_lip_front    = 2.0;   // front retaining lip that traps the PCB
cam_window_d     = 16.0;  // front opening — clears the lens & its field of view
cam_ribbon_w     = 17.0;  // FFC ribbon exit slot at the pocket bottom

cam_foot_len     = 40;    // foot length along the rail (X)
cam_base_t       = 6;     // foot thickness
cam_plate_t      = 6;     // vertical plate thickness (>= ins_m2_h so inserts fit)
cam_plate_h      = 40;    // vertical plate height
cam_plate_w      = 34;    // vertical plate width

cam_slot_len     = 30;    // baseline-adjust travel (slotted foot hole)
cam_toe_in_deg   = 0;     // 0 = parallel optics (rectified stereo). small toe ok
cam_use_screws   = true;  // M2 heat-set inserts to bolt the PCB down
cam_hole_dx      = 21;    // PCB mount-hole horizontal spacing — MEASURE yours
cam_hole_dz      = 0;     // vertical offset of the mount holes from pocket center

cam_center_z     = cam_base_t + cam_plate_h / 2;   // PCB centre height (derived)

// =============================================================================
//  ELECTRONICS TRAY — holds the Jetson Orin Nano + the MCP4922/op-amp protoboard
//  Not stiffness-critical (no optics on it), so it's a light plate with standoff
//  bosses. Bolts to the frame with M5 T-nut bolts. MEASURE your board hole
//  patterns — the Jetson dev-kit pattern below is nominal.
// =============================================================================
tray_w           = 172;   // tray plate width  (X)
tray_d           = 108;   // tray plate depth  (Y)
tray_t           = 4;     // tray plate thickness
tray_standoff_h  = 6;     // standoff height (clears solder/leads under boards)
tray_standoff_d  = 7.5;   // standoff outer diameter
tray_edge        = 9;     // frame-bolt inset from the tray edge

// Jetson Orin Nano dev-kit carrier mounting holes (nominal — MEASURE yours):
tray_jetson_dx   = 86;    // hole spacing X
tray_jetson_dy   = 58;    // hole spacing Y
tray_jetson_cx   = -34;   // pattern centre offset on the tray (X)
tray_jetson_ins  = ins_m3_d;
tray_jetson_ins_h= ins_m3_h;

// Aux protoboard (MCP4922 DAC + op-amp level-shift) mounting holes:
tray_aux_dx      = 45;
tray_aux_dy      = 35;
tray_aux_cx      = 56;    // pattern centre offset on the tray (X)
tray_aux_ins     = ins_m3_d;
tray_aux_ins_h   = ins_m3_h;

// =============================================================================
//  PHOTODIODE / LENS HOLDER — the wing-beat sensor front-end (§3.2)
//  A short barrel: a collecting lens up front, the photodiode at its focal
//  distance behind it, on a slotted foot that clamps to the rail and aims at the
//  detection volume. Optics are cheap and non-critical — this just gathers flux.
// =============================================================================
pd_lens_od       = 12.0;  // collecting lens outer diameter (MEASURE)
pd_lens_t        = 3.0;   // lens seat depth (front counterbore)
pd_focal         = 20.0;  // lens focal length ≈ lens-to-photodiode distance
pd_barrel_wall   = 3.0;   // barrel wall thickness
pd_body_w        = 5.8;   // BPW34 body pocket width  (MEASURE)
pd_body_h        = 4.7;   // BPW34 body pocket height
pd_body_t        = 3.6;   // BPW34 body pocket depth
pd_lead_d        = 5.0;   // rear clearance for leads / wiring
pd_foot_len      = 26;    // foot length along the rail
pd_base_t        = 6;     // foot thickness
pd_slot_len      = 18;    // aim-position adjust travel on the rail
pd_aim_deg       = 0;     // barrel tilt toward the volume (0 = level, -Y look)
