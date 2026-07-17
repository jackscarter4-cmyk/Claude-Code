// =============================================================================
//  BUGLOCK — stereo camera mount  (docs/laser-fly-turret-design.md §4.5 C2/C4)
//  An L-bracket that clamps to the TOP of a horizontal 2020 rail. The foot has a
//  SLOT so you slide it along the rail to an exact 60 mm baseline (baseline is
//  carried by the ALUMINUM, not the plastic) and lock it with one M5 T-nut bolt.
//  The Pi Cam v2 PCB drops into a lipped pocket (a repeatable datum) and is held
//  by two M2 heat-set inserts. Print TWO. Lens looks down the -Y axis (downrange).
//
//  Not compile-checked in the build sandbox (no OpenSCAD) — verify in OpenSCAD /
//  Bambu Studio before printing.
// =============================================================================
include <params.scad>

module insert_hole(d, h) { cylinder(d = d, h = h); }

// cam_center_z is defined in params.scad (derived from cam_base_t + cam_plate_h)

// PCB pocket, built about its own centre (back face at Y=0, lens out -Y) --------
module cam_pocket() {
    ow = cam_pcb_w + 2 * cam_lip_w;
    oh = cam_pcb_h + 2 * cam_lip_w;
    od = cam_pcb_t + cam_pocket_clr + cam_lip_front;   // total depth in Y
    difference() {
        // outer frame
        translate([-ow / 2, -od, -oh / 2]) cube([ow, od, oh]);

        // PCB cavity: open at the back (onto the plate) and open at the TOP to
        // slide the board in; front lip (cam_lip_front) retains it.
        translate([-(cam_pcb_w + cam_pocket_clr) / 2,
                   -(cam_pcb_t + cam_pocket_clr),
                   -(cam_pcb_h + cam_pocket_clr) / 2])
            cube([cam_pcb_w + cam_pocket_clr,
                  cam_pcb_t + cam_pocket_clr + 0.2,
                  (cam_pcb_h + cam_pocket_clr) / 2 + oh]);   // extend up = open top

        // front window: clears the lens and its field of view
        translate([0, -od - 0.1, 0]) rotate([-90, 0, 0])
            cylinder(d = cam_window_d, h = cam_lip_front + 0.4);

        // ribbon (FFC) exit slot through the bottom lip
        translate([-cam_ribbon_w / 2, -od - 0.1, -oh / 2 - 0.1])
            cube([cam_ribbon_w, od + 0.2, cam_lip_w + 0.2]);
    }
}

module foot_slot() {
    translate([0, ext_w / 2, -0.1]) {
        hull() {
            translate([-cam_slot_len / 2, 0, 0]) cylinder(d = m5_clear, h = cam_base_t + 0.2);
            translate([ cam_slot_len / 2, 0, 0]) cylinder(d = m5_clear, h = cam_base_t + 0.2);
        }
        translate([0, 0, cam_base_t - m5_head_h + 0.1])
            hull() {
                translate([-cam_slot_len / 2, 0, 0]) cylinder(d = m5_head, h = m5_head_h + 0.3);
                translate([ cam_slot_len / 2, 0, 0]) cylinder(d = m5_head, h = m5_head_h + 0.3);
            }
    }
}

module camera_mount() {
    difference() {
        union() {
            // foot on the rail
            translate([-cam_foot_len / 2, 0, 0]) cube([cam_foot_len, ext_w, cam_base_t]);
            // vertical plate (merges with the foot front)
            translate([-cam_plate_w / 2, 0, 0]) cube([cam_plate_w, cam_plate_t, cam_base_t + cam_plate_h]);
            // camera pocket, placed at PCB height and toed in if requested
            translate([0, 0, cam_center_z]) rotate([0, 0, cam_toe_in_deg]) cam_pocket();
        }

        // baseline-adjust slot in the foot
        foot_slot();

        // M2 inserts to bolt the PCB to the plate (from the front)
        if (cam_use_screws)
            for (sx = [-1, 1])
                translate([0, 0, cam_center_z]) rotate([0, 0, cam_toe_in_deg])
                    translate([sx * cam_hole_dx / 2, -0.2, cam_hole_dz])
                        rotate([-90, 0, 0]) insert_hole(ins_m2_d, ins_m2_h + 0.3);
    }
}

camera_mount();
