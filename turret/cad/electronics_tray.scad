// =============================================================================
//  BUGLOCK — electronics tray  (holds Jetson Orin Nano + MCP4922/op-amp board)
//  Not a stiffness-critical part (no optics ride on it), so it's a light plate
//  with standoff bosses. Bolts to the frame with M5 T-nut bolts (counterbored
//  from the top). Print PETG/ASA, plate down. MEASURE your board hole patterns
//  and edit params.scad before printing.
//
//  Not compile-checked in the build sandbox (no OpenSCAD) — verify before print.
// =============================================================================
include <params.scad>

module standoff(ins_d, ins_h) {
    difference() {
        cylinder(d = tray_standoff_d, h = tray_t + tray_standoff_h);
        // heat-set insert hole, blind from the top
        translate([0, 0, tray_t + tray_standoff_h - ins_h])
            cylinder(d = ins_d, h = ins_h + 0.2);
    }
}

module hole_pattern(cx, dx, dy, ins_d, ins_h) {
    for (sx = [-1, 1], sy = [-1, 1])
        translate([cx + sx * dx / 2, sy * dy / 2, 0])
            standoff(ins_d, ins_h);
}

module electronics_tray() {
    difference() {
        union() {
            // base plate
            translate([-tray_w / 2, -tray_d / 2, 0]) cube([tray_w, tray_d, tray_t]);
            // board standoffs
            hole_pattern(tray_jetson_cx, tray_jetson_dx, tray_jetson_dy,
                         tray_jetson_ins, tray_jetson_ins_h);
            hole_pattern(tray_aux_cx, tray_aux_dx, tray_aux_dy,
                         tray_aux_ins, tray_aux_ins_h);
        }

        // frame hold-down M5 holes with top counterbore, at the four corners
        for (sx = [-1, 1], sy = [-1, 1])
            translate([sx * (tray_w / 2 - tray_edge),
                       sy * (tray_d / 2 - tray_edge), -0.1]) {
                cylinder(d = m5_clear, h = tray_t + 0.2);
                translate([0, 0, tray_t - m5_head_h])
                    cylinder(d = m5_head, h = m5_head_h + 0.2);
            }

        // two cable / zip-tie pass-through slots between the boards
        for (sy = [-1, 1])
            translate([tray_jetson_cx + tray_jetson_dx / 2 + 12,
                       sy * 22, -0.1])
                hull() {
                    translate([-4, 0, 0]) cylinder(d = 5, h = tray_t + 0.2);
                    translate([ 4, 0, 0]) cylinder(d = 5, h = tray_t + 0.2);
                }
    }
}

electronics_tray();
