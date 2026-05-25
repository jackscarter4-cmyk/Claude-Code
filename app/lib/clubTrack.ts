// Experimental clubhead tracking by frame-to-frame motion.
//
// The clubhead is the fastest-moving thing in a swing, so between two
// consecutive frames it produces the strongest pixel change in a ring around
// the hands. We find the motion centroid in that ring and call it the
// clubhead. This is noisy — it shines on the fast downswing and is unreliable
// at address/top (little motion) — so callers should treat null as "unknown".

export type GrayFrame = { data: Uint8Array; w: number; h: number };

export function detectClubhead(
  prev: GrayFrame,
  cur: GrayFrame,
  wristXNorm: number,
  wristYNorm: number,
  clubLenNorm: number, // estimated club length as a fraction of image width
): { x: number; y: number } | null {
  const { w, h, data } = cur;
  if (prev.w !== w || prev.h !== h) return null;

  const wx = wristXNorm * w;
  const wy = wristYNorm * h;

  // Search a ring from just past the hands out to a bit beyond club length.
  const minR = Math.max(2, 0.2 * clubLenNorm * w);
  const maxR = Math.max(minR + 2, 1.35 * clubLenNorm * w);
  const minR2 = minR * minR;
  const maxR2 = maxR * maxR;

  const THRESH = 28; // per-pixel luminance change to count as motion
  let sx = 0;
  let sy = 0;
  let sw = 0;
  let count = 0;

  const x0 = Math.max(0, Math.floor(wx - maxR));
  const x1 = Math.min(w - 1, Math.ceil(wx + maxR));
  const y0 = Math.max(0, Math.floor(wy - maxR));
  const y1 = Math.min(h - 1, Math.ceil(wy + maxR));

  for (let y = y0; y <= y1; y++) {
    const dyp = y - wy;
    const row = y * w;
    for (let x = x0; x <= x1; x++) {
      const dxp = x - wx;
      const r2 = dxp * dxp + dyp * dyp;
      if (r2 < minR2 || r2 > maxR2) continue;
      const i = row + x;
      const d = Math.abs(cur.data[i] - prev.data[i]);
      if (d < THRESH) continue;
      const wgt = d * d;
      sx += x * wgt;
      sy += y * wgt;
      sw += wgt;
      count++;
    }
  }
  if (sw === 0 || count < 4) return null;
  void data;
  return { x: sx / sw / w, y: sy / sw / h };
}
