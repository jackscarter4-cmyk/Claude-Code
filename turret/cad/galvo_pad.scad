// =============================================================================
//  BUGLOCK — galvo mounting pad  (docs/laser-fly-turret-design.md §4.5 C1)
//  A short, solid pedestal: the galvo scanner block bolts to the TOP face via
//  heat-set inserts; four M5 hold-downs (counterbored from the top) bolt the pad
//  to a baseplate or a 2040/2020 cross-rail below.  Stiffness dominates: keep it
//  short and thick.  Print in PETG/ASA, base flat on the plate.
//
//  I could not compile-check this in the build sandbox (no OpenSCAD installed) —
//  open it in OpenSCAD or import to Bambu Studio to confirm before printing.
// =============================================================================
include <params.scad>

module insert_hole(d, h) {          // blind melt-in hole for a heat-set insert
    cylinder(d = d, h = h);
}

module galvo_pad() {
    pw    = galvo_block_w + 2 * galvo_margin;   // pad footprint X
    pd    = galvo_block_d + 2 * galvo_margin;   // pad footprint Y
    hd_dx = pw - 2 * galvo_hold_edge;           // hold-down spacing X
    hd_dy = pd - 2 * galvo_hold_edge;           // hold-down spacing Y

    difference() {
        // ---- solid pedestal (load in compression) ----
        translate([-pw / 2, -pd / 2, 0])
            cube([pw, pd, galvo_ped_h]);

        // ---- galvo block bolt pattern: heat-set inserts in the TOP face ----
        for (sx = [-1, 1], sy = [-1, 1])
            translate([sx * galvo_bolt_dx / 2,
                       sy * galvo_bolt_dy / 2,
                       galvo_ped_h - galvo_bolt_ins_h])
                insert_hole(galvo_bolt_ins, galvo_bolt_ins_h + 0.2);

        // ---- hold-down M5 through-holes with top counterbore (at corners) ----
        for (sx = [-1, 1], sy = [-1, 1])
            translate([sx * hd_dx / 2, sy * hd_dy / 2, -0.1]) {
                cylinder(d = m5_clear, h = galvo_ped_h + 0.2);
                translate([0, 0, galvo_ped_h - m5_head_h])
                    cylinder(d = m5_head, h = m5_head_h + 0.2);
            }

        // ---- cable relief channel across the base ----
        translate([-cable_slot_w / 2, -pd / 2 - 0.1, -0.1])
            cube([cable_slot_w, pd + 0.2, cable_slot_h]);
    }
}

galvo_pad();
