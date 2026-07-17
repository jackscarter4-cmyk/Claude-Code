// =============================================================================
//  BUGLOCK — print layout for the Bambu Lab P1S (256 x 256 mm bed)
//  All three printed brackets arranged flat, foot/base down. Everything fits the
//  P1S bed with room to spare. Open in OpenSCAD, export STL, slice in Bambu
//  Studio. (Or open each part file on its own to print singly.)
// =============================================================================
include <params.scad>
use <galvo_pad.scad>
use <camera_mount.scad>

// two camera mounts (left + right of the stereo pair)
translate([-70,  40, 0]) camera_mount();
translate([  0,  40, 0]) camera_mount();

// galvo pad
translate([  0, -55, 0]) galvo_pad();

// The electronics tray and photodiode holder are printed from their own files
// (electronics_tray.scad, photodiode_holder.scad) — the tray is large and the
// holder slices best on its own. Uncomment to co-plate if they fit your run:
// use <electronics_tray.scad>
// use <photodiode_holder.scad>
