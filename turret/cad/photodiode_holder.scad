// =============================================================================
//  BUGLOCK — photodiode / lens holder  (wing-beat sensor front-end, §3.2)
//  A short barrel: a collecting lens seats in the front, the BPW34 photodiode
//  sits at the lens focal distance behind it, on a slotted foot that clamps to
//  the rail and aims at the detection volume (lens looks down the -Y axis).
//  Optics are cheap and non-critical — this only gathers flux for the FFT gate.
//  Print PETG/ASA. Not compile-checked in-sandbox — verify before printing.
// =============================================================================
include <params.scad>

barrel_od  = pd_lens_od + 2 * pd_barrel_wall;
barrel_len = pd_lens_t + pd_focal + pd_body_t + 2;   // +2 = rear wall
aperture_d = pd_lens_od - 2;

// Barrel built along +Z: z=0 is the rear wall, z=barrel_len is the lens face.
module pd_barrel() {
    difference() {
        cylinder(d = barrel_od, h = barrel_len);

        // front lens seat (press-fit counterbore)
        translate([0, 0, barrel_len - pd_lens_t])
            cylinder(d = pd_lens_od, h = pd_lens_t + 0.1);

        // internal light path (aperture tube) from behind the lens to the pocket
        translate([0, 0, 2 + pd_body_t])
            cylinder(d = aperture_d, h = barrel_len - pd_lens_t - (2 + pd_body_t) + 0.1);

        // photodiode body pocket (rectangular), just inside the rear wall
        translate([-pd_body_w / 2, -pd_body_h / 2, 2])
            cube([pd_body_w, pd_body_h, pd_body_t + 0.1]);

        // rear lead / wire clearance through the back wall
        translate([0, 0, -0.1]) cylinder(d = pd_lead_d, h = 2 + 0.2);
    }
}

module pd_foot_slot() {
    translate([0, ext_w / 2, -0.1]) {
        hull() {
            translate([-pd_slot_len / 2, 0, 0]) cylinder(d = m5_clear, h = pd_base_t + 0.2);
            translate([ pd_slot_len / 2, 0, 0]) cylinder(d = m5_clear, h = pd_base_t + 0.2);
        }
        translate([0, 0, pd_base_t - m5_head_h + 0.1])
            hull() {
                translate([-pd_slot_len / 2, 0, 0]) cylinder(d = m5_head, h = m5_head_h + 0.3);
                translate([ pd_slot_len / 2, 0, 0]) cylinder(d = m5_head, h = m5_head_h + 0.3);
            }
    }
}

module photodiode_holder() {
    z0 = pd_base_t + barrel_od / 2 - 1.5;   // barrel axis height (embed into foot)
    difference() {
        union() {
            translate([-pd_foot_len / 2, 0, 0]) cube([pd_foot_len, ext_w, pd_base_t]);
            translate([0, ext_w / 2, z0])
                rotate([pd_aim_deg, 0, 0])   // aim up/down toward the volume
                    rotate([90, 0, 0])       // point the barrel down the -Y axis
                        pd_barrel();
        }
        pd_foot_slot();
    }
}

photodiode_holder();
