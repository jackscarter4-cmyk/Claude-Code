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
