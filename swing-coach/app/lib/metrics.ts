import savitzkyGolay from "ml-savitzky-golay";
import type { Frame } from "./db";
import { CLUB, SWING } from "./thresholds";

// MediaPipe BlazePose landmark indices we use.
const LM = {
  NOSE: 0,
  L_EAR: 7,
  R_EAR: 8,
  L_SHOULDER: 11,
  R_SHOULDER: 12,
  L_ELBOW: 13,
  R_ELBOW: 14,
  L_WRIST: 15,
  R_WRIST: 16,
  L_HIP: 23,
  R_HIP: 24,
} as const;

export type Phases = {
  P1: { frame: number; t_ms: number } | null;
  P4: { frame: number; t_ms: number } | null;
  P7: { frame: number; t_ms: number } | null;
};

export type KinematicSequence = {
  pelvisPeakMs: number | null;
  thoraxPeakMs: number | null;
  armPeakMs: number | null;
  clubPeakMs: number | null;
  order: ("pelvis" | "thorax" | "arm" | "club")[];
  correct: boolean;
};

export type LeadHipDisplacement = {
  dx_norm: number;
  dz_norm: number;
  earlyExtension: boolean;
};

export type ClubMetrics = {
  // 2D-extrapolated clubhead speed; honest proxy, ~15-20% error.
  peakSpeedMph: number;
  peakSpeedMs: number;
  // clubhead acceleration * head mass at its peak. A lag/load INDICATOR,
  // not a shaft-flex/stiffness measurement (video can't recover flex).
  shaftLoadProxy: number;
};

export type RotationMetrics = {
  // Foreshortening-based axial rotation at the top of the backswing (P4).
  pelvisRotationDegP4: number;
  shoulderRotationDegP4: number;
  xFactorProxyDeg: number; // shoulder - pelvis at P4 (label as proxy)
};

// Per-frame normalized (0..1) segment speeds for the transfer heatmap.
export type SequenceSeries = {
  t_ms: number[];
  pelvis: number[];
  thorax: number[];
  arm: number[];
  club: number[];
};

export type Measurements = {
  phases: Phases;
  shoulderWidthPx: number;
  pixelsPerMeter: number;
  handedness: "right" | "left";
  metrics: {
    kinematicSequence: KinematicSequence;
    leadHipDisplacement: LeadHipDisplacement;
    spineAngleChangeDeg: number;
    headSwayNorm: number;
    rotation: RotationMetrics;
    club: ClubMetrics;
  };
  sequenceSeries: SequenceSeries | null;
};

function emptyMeasurements(shoulderWidthPx = 0): Measurements {
  return {
    phases: { P1: null, P4: null, P7: null },
    shoulderWidthPx,
    pixelsPerMeter: 0,
    handedness: "right",
    metrics: {
      kinematicSequence: {
        pelvisPeakMs: null,
        thoraxPeakMs: null,
        armPeakMs: null,
        clubPeakMs: null,
        order: [],
        correct: false,
      },
      leadHipDisplacement: { dx_norm: 0, dz_norm: 0, earlyExtension: false },
      spineAngleChangeDeg: 0,
      headSwayNorm: 0,
      rotation: {
        pelvisRotationDegP4: 0,
        shoulderRotationDegP4: 0,
        xFactorProxyDeg: 0,
      },
      club: { peakSpeedMph: 0, peakSpeedMs: 0, shaftLoadProxy: 0 },
    },
    sequenceSeries: null,
  };
}

function safeNum(n: number): number {
  return Number.isFinite(n) ? n : 0;
}

/** Smooth a 1D array with Savitzky-Golay; keeps length via pre-padding. */
function smooth(data: number[]): number[] {
  if (data.length < 5) return data.slice();
  // Use a window that fits the data, odd and >=5.
  let windowSize = 9;
  let polynomial = 3;
  if (data.length < 20) {
    windowSize = 5;
    polynomial = 2;
  }
  if (windowSize > data.length) {
    windowSize = data.length % 2 === 0 ? data.length - 1 : data.length;
    if (windowSize < 5) return data.slice();
    if (polynomial >= windowSize) polynomial = Math.max(1, windowSize - 2);
  }
  try {
    return savitzkyGolay(data, 1, {
      windowSize,
      polynomial,
      derivative: 0,
      pad: "pre",
      padValue: "replicate",
    });
  } catch {
    return data.slice();
  }
}

/** Numerical derivative using central differences over t_ms (per ms). */
function derivative(values: number[], times: number[]): number[] {
  const n = values.length;
  const out = new Array<number>(n).fill(0);
  if (n < 2) return out;
  for (let i = 0; i < n; i++) {
    if (i === 0) {
      const dt = times[1] - times[0] || 1;
      out[i] = (values[1] - values[0]) / dt;
    } else if (i === n - 1) {
      const dt = times[n - 1] - times[n - 2] || 1;
      out[i] = (values[n - 1] - values[n - 2]) / dt;
    } else {
      const dt = times[i + 1] - times[i - 1] || 1;
      out[i] = (values[i + 1] - values[i - 1]) / dt;
    }
  }
  return out;
}

function stddev(arr: number[]): number {
  if (arr.length === 0) return 0;
  const mean = arr.reduce((a, b) => a + b, 0) / arr.length;
  const v = arr.reduce((a, b) => a + (b - mean) * (b - mean), 0) / arr.length;
  return Math.sqrt(v);
}

function lmVis(frames: Frame[], idx: number): number {
  let sum = 0;
  let count = 0;
  for (const f of frames) {
    const lm = f.landmarks[idx];
    if (!lm) continue;
    sum += lm.visibility ?? 0;
    count++;
  }
  return count === 0 ? 0 : sum / count;
}

function meanShoulderWidthPx(frames: Frame[], W: number): number {
  let sum = 0;
  let count = 0;
  for (const f of frames) {
    const l = f.landmarks[LM.L_SHOULDER];
    const r = f.landmarks[LM.R_SHOULDER];
    if (!l || !r) continue;
    if ((l.visibility ?? 0) < 0.5 || (r.visibility ?? 0) < 0.5) continue;
    const dx = (l.x - r.x) * W;
    const dy = (l.y - r.y) * W; // intentionally use W to keep px metric consistent across aspect
    sum += Math.hypot(dx, dy);
    count++;
  }
  return count === 0 ? 0 : sum / count;
}

/** Average of both wrists' y in pixels; NaN where neither wrist is visible. */
function avgWristYPx(frames: Frame[], H: number): number[] {
  const out = new Array<number>(frames.length).fill(NaN);
  for (let i = 0; i < frames.length; i++) {
    const lw = frames[i].landmarks[LM.L_WRIST];
    const rw = frames[i].landmarks[LM.R_WRIST];
    const vals: number[] = [];
    if (lw && (lw.visibility ?? 0) > 0.4) vals.push(lw.y * H);
    if (rw && (rw.visibility ?? 0) > 0.4) vals.push(rw.y * H);
    if (vals.length > 0) out[i] = vals.reduce((a, b) => a + b, 0) / vals.length;
  }
  // Fill gaps with nearest valid value so smoothing/gradient stay stable.
  let lastValid = NaN;
  for (let i = 0; i < out.length; i++) {
    if (Number.isNaN(out[i])) out[i] = lastValid;
    else lastValid = out[i];
  }
  let next = NaN;
  for (let i = out.length - 1; i >= 0; i--) {
    if (Number.isNaN(out[i])) out[i] = next;
    else next = out[i];
  }
  for (let i = 0; i < out.length; i++) if (Number.isNaN(out[i])) out[i] = 0;
  return out;
}

/** Central-difference gradient with unit spacing. */
function gradient(arr: number[]): number[] {
  const n = arr.length;
  const out = new Array<number>(n).fill(0);
  if (n < 2) return out;
  out[0] = arr[1] - arr[0];
  out[n - 1] = arr[n - 1] - arr[n - 2];
  for (let i = 1; i < n - 1; i++) out[i] = (arr[i + 1] - arr[i - 1]) / 2;
  return out;
}

function percentile(values: number[], p: number): number {
  const arr = values.filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  if (arr.length === 0) return 0;
  const idx = Math.min(arr.length - 1, Math.max(0, Math.round((p / 100) * (arr.length - 1))));
  return arr[idx];
}

/**
 * Detect P1 (address), P4 (top), P7 (impact) from the averaged wrist-Y
 * trajectory. Adapted from the GolfPosePro heuristics:
 *  - average both wrists (robust to occlusion, handedness-free)
 *  - swing start = first frame whose wrist speed exceeds the 90th percentile
 *  - swing end = wrist returns to address height after the speed peak
 *  - address = walk back from swing start while wrist-Y stays flat
 *  - top = highest hands before the speed peak
 *  - impact = lowest hands between top and swing end
 */
function detectPhases(
  frames: Frame[],
  H: number,
  shoulderWidthPx: number,
): Phases {
  const n = frames.length;
  if (n < 10) return { P1: null, P4: null, P7: null };

  const totalMs = frames[n - 1].t_ms - frames[0].t_ms;
  const fps = totalMs > 0 ? ((n - 1) * 1000) / totalMs : 30;

  const wristY = avgWristYPx(frames, H);
  const smoothed = smooth(wristY);
  const velMag = gradient(smoothed).map((v) => Math.abs(v));

  // Skip a still intro: if the first frames barely move, start searching later.
  const precheck = Math.max(3, Math.min(Math.round(fps), Math.floor(n * 0.3)));
  const earlyMean =
    velMag.slice(0, precheck).reduce((a, b) => a + b, 0) / precheck;
  const medianVel = percentile(velMag, 50);
  const buffer = earlyMean < 0.1 * medianVel ? precheck : 0;

  const threshold = percentile(velMag.slice(buffer), 90);
  const motionIdx: number[] = [];
  for (let i = buffer + 1; i < n; i++) if (velMag[i] > threshold) motionIdx.push(i);

  if (motionIdx.length === 0) return { P1: null, P4: null, P7: null };

  const swingStart = motionIdx[0];
  let peakIdx = motionIdx[0];
  for (const i of motionIdx) if (velMag[i] > velMag[peakIdx]) peakIdx = i;

  // Swing end: wrist returns closest to its address height after the peak.
  const addressY = smoothed[swingStart];
  let swingEnd = n - 1;
  {
    let best = Infinity;
    for (let i = peakIdx + 1; i < n; i++) {
      const d = Math.abs(smoothed[i] - addressY);
      if (d < best) {
        best = d;
        swingEnd = i;
      }
    }
  }

  // Address: walk back from swing start while wrist-Y stays flat.
  const flatThresh = Math.max(1, 0.03 * shoulderWidthPx);
  const minFlat = Math.max(3, Math.round(fps * 0.2));
  let addressStart = Math.max(0, swingStart - minFlat);
  for (let i = swingStart - minFlat; i >= 0; i--) {
    const window = smoothed.slice(i, swingStart);
    if (window.length < minFlat) continue;
    if (stddev(window) < flatThresh) addressStart = i;
    else break;
  }
  const p1Frame = Math.floor((addressStart + swingStart) / 2);

  // Top of backswing: highest hands (min y) before the speed peak.
  let topIdx = swingStart;
  for (let i = swingStart; i <= peakIdx && i < n; i++) {
    if (smoothed[i] < smoothed[topIdx]) topIdx = i;
  }

  // Impact: lowest hands (max y) between the top and swing end.
  let impactIdx = topIdx;
  for (let i = topIdx; i <= swingEnd && i < n; i++) {
    if (smoothed[i] > smoothed[impactIdx]) impactIdx = i;
  }

  return {
    P1: { frame: p1Frame, t_ms: frames[p1Frame].t_ms },
    P4: { frame: topIdx, t_ms: frames[topIdx].t_ms },
    P7: { frame: impactIdx, t_ms: frames[impactIdx].t_ms },
  };
}

function angularVelocityFromLine(
  frames: Frame[],
  startFrame: number,
  endFrame: number,
  W: number,
  H: number,
  idxA: number,
  idxB: number,
): { times: number[]; angVel: number[] } {
  const n = endFrame - startFrame + 1;
  const angles = new Array<number>(n).fill(0);
  const times = new Array<number>(n).fill(0);
  for (let i = 0; i < n; i++) {
    const f = frames[startFrame + i];
    const a = f.landmarks[idxA];
    const b = f.landmarks[idxB];
    times[i] = f.t_ms;
    if (!a || !b) {
      angles[i] = i > 0 ? angles[i - 1] : 0;
      continue;
    }
    const dx = (b.x - a.x) * W;
    const dy = (b.y - a.y) * H;
    angles[i] = Math.atan2(dy, dx);
  }
  // Unwrap angles to avoid 2pi discontinuities.
  for (let i = 1; i < n; i++) {
    let diff = angles[i] - angles[i - 1];
    while (diff > Math.PI) {
      angles[i] -= 2 * Math.PI;
      diff = angles[i] - angles[i - 1];
    }
    while (diff < -Math.PI) {
      angles[i] += 2 * Math.PI;
      diff = angles[i] - angles[i - 1];
    }
  }
  const angSmooth = smooth(angles);
  const angVelRaw = derivative(angSmooth, times); // rad/ms
  const angVel = angVelRaw.map((v) => Math.abs(v));
  return { times, angVel };
}

function wristSpeed(
  frames: Frame[],
  startFrame: number,
  endFrame: number,
  W: number,
  H: number,
  wristIdx: number,
): { times: number[]; speed: number[] } {
  const n = endFrame - startFrame + 1;
  const xs = new Array<number>(n).fill(0);
  const ys = new Array<number>(n).fill(0);
  const times = new Array<number>(n).fill(0);
  for (let i = 0; i < n; i++) {
    const f = frames[startFrame + i];
    const lm = f.landmarks[wristIdx];
    times[i] = f.t_ms;
    xs[i] = lm ? lm.x * W : i > 0 ? xs[i - 1] : 0;
    ys[i] = lm ? lm.y * H : i > 0 ? ys[i - 1] : 0;
  }
  const xsS = smooth(xs);
  const ysS = smooth(ys);
  const vx = derivative(xsS, times);
  const vy = derivative(ysS, times);
  const speed = vx.map((v, i) => Math.hypot(v, vy[i]));
  return { times, speed };
}

function peakTime(times: number[], values: number[]): number | null {
  if (times.length === 0 || values.length === 0) return null;
  let maxIdx = 0;
  let maxVal = -Infinity;
  for (let i = 0; i < values.length; i++) {
    if (values[i] > maxVal) {
      maxVal = values[i];
      maxIdx = i;
    }
  }
  return times[maxIdx] ?? null;
}

function midpoint(
  a: { x: number; y: number; z: number; visibility?: number },
  b: { x: number; y: number; z: number; visibility?: number },
) {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, z: (a.z + b.z) / 2 };
}

/** Pixel distance between two landmarks (uses W for both axes for a stable scale). */
function segWidthPx(frame: Frame, idxA: number, idxB: number, W: number): number {
  const a = frame.landmarks[idxA];
  const b = frame.landmarks[idxB];
  if (!a || !b) return NaN;
  if ((a.visibility ?? 0) < 0.4 || (b.visibility ?? 0) < 0.4) return NaN;
  return Math.hypot((a.x - b.x) * W, (a.y - b.y) * W);
}

/**
 * Axial rotation (degrees) of a body line about the vertical, recovered from
 * foreshortening. A line of fixed true length projects to width
 * w(t) = w_facingCamera * cos(rotation), so rotation = arccos(w(t)/w_max).
 * Returns per-frame degrees and matching timestamps over [startFrame,endFrame].
 */
function rotationDegSeries(
  frames: Frame[],
  startFrame: number,
  endFrame: number,
  idxA: number,
  idxB: number,
  W: number,
): { times: number[]; deg: number[] } {
  const widths: number[] = [];
  for (let i = 0; i < frames.length; i++) {
    widths.push(segWidthPx(frames[i], idxA, idxB, W));
  }
  const valid = widths.filter((w) => Number.isFinite(w));
  const wMax = valid.length ? Math.max(...valid) : 1;
  // Fill gaps with nearest valid so the series is continuous.
  let last = wMax;
  for (let i = 0; i < widths.length; i++) {
    if (!Number.isFinite(widths[i])) widths[i] = last;
    else last = widths[i];
  }
  const degAll = widths.map((w) => {
    const ratio = wMax > 0 ? Math.min(1, Math.max(0, w / wMax)) : 1;
    return (Math.acos(ratio) * 180) / Math.PI;
  });
  const degSmooth = smooth(degAll);
  const times: number[] = [];
  const deg: number[] = [];
  for (let i = startFrame; i <= endFrame && i < frames.length; i++) {
    times.push(frames[i].t_ms);
    deg.push(degSmooth[i]);
  }
  return { times, deg };
}

/**
 * Extrapolate the (untracked) clubhead from the lead-arm line: the shaft runs
 * roughly along elbow->wrist, extended by the club's real length scaled to
 * pixels. Returns normalized image coords for drawing, or null.
 */
export function extrapolateClubhead(
  landmarks: { x: number; y: number; visibility?: number }[],
  leadSide: "left" | "right",
  shoulderWidthPx: number,
  W: number,
  H: number,
): { x: number; y: number } | null {
  const elbowIdx = leadSide === "left" ? LM.L_ELBOW : LM.R_ELBOW;
  const wristIdx = leadSide === "left" ? LM.L_WRIST : LM.R_WRIST;
  const elbow = landmarks[elbowIdx];
  const wrist = landmarks[wristIdx];
  if (!elbow || !wrist || shoulderWidthPx <= 0) return null;
  const pxPerM = shoulderWidthPx / CLUB.biacromialWidthM;
  const clubLenPx = CLUB.shaftLengthM * pxPerM;
  let dx = (wrist.x - elbow.x) * W;
  let dy = (wrist.y - elbow.y) * H;
  const len = Math.hypot(dx, dy);
  if (len < 1e-6) return null;
  dx /= len;
  dy /= len;
  return {
    x: wrist.x + (dx * clubLenPx) / W,
    y: wrist.y + (dy * clubLenPx) / H,
  };
}

function headCenter(
  frame: Frame,
): { x: number; y: number; z: number } | null {
  const le = frame.landmarks[LM.L_EAR];
  const re = frame.landmarks[LM.R_EAR];
  if (le && re && (le.visibility ?? 0) > 0.3 && (re.visibility ?? 0) > 0.3) {
    return midpoint(le, re);
  }
  const nose = frame.landmarks[LM.NOSE];
  if (nose) return { x: nose.x, y: nose.y, z: nose.z };
  return null;
}

function spineAngleDeg(frame: Frame): number | null {
  const ls = frame.landmarks[LM.L_SHOULDER];
  const rs = frame.landmarks[LM.R_SHOULDER];
  const lh = frame.landmarks[LM.L_HIP];
  const rh = frame.landmarks[LM.R_HIP];
  if (!ls || !rs || !lh || !rh) return null;
  const ms = midpoint(ls, rs);
  const mh = midpoint(lh, rh);
  // Vector from hip to shoulder. y is inverted (down positive), so -dy gives upward.
  const dx = ms.x - mh.x;
  const dy = ms.y - mh.y;
  // atan2(dx, -dy): 0 = vertical (upright), positive = tilted toward +x.
  const rad = Math.atan2(dx, -dy);
  return (rad * 180) / Math.PI;
}

export function computeMeasurements(
  frames: Frame[],
  videoWidth: number,
  videoHeight: number,
): Measurements {
  if (!frames || frames.length < 10) {
    return emptyMeasurements();
  }
  const W = videoWidth > 0 ? videoWidth : 1920;
  const H = videoHeight > 0 ? videoHeight : 1080;

  // TODO: implement true handedness detection. For now default right-handed
  // (lead = left side). User can override later.
  const handedness: "right" | "left" = "right";
  const leadSide: "left" | "right" = handedness === "right" ? "left" : "right";

  // Estimate shoulder width across all visible frames first (fallback).
  let shoulderWidthPx = meanShoulderWidthPx(frames, W);
  if (shoulderWidthPx <= 0) {
    // Last-resort fallback: 1/6 of frame width.
    shoulderWidthPx = W / 6;
  }

  // Phase detection (wrist-Y based; handedness-free).
  const phases = detectPhases(frames, H, shoulderWidthPx);
  const P1 = phases.P1;

  // Refine shoulder width using P1 frame if available.
  if (P1) {
    const f = frames[P1.frame];
    const l = f.landmarks[LM.L_SHOULDER];
    const r = f.landmarks[LM.R_SHOULDER];
    if (
      l &&
      r &&
      (l.visibility ?? 0) > 0.5 &&
      (r.visibility ?? 0) > 0.5
    ) {
      const dx = (l.x - r.x) * W;
      const dy = (l.y - r.y) * W;
      const w = Math.hypot(dx, dy);
      if (w > 0) shoulderWidthPx = w;
    }
  }

  const P4 = phases.P4;
  const P7 = phases.P7;

  const pixelsPerMeter = shoulderWidthPx / CLUB.biacromialWidthM;

  // If we couldn't determine P4 or P7 reliably, return what we have.
  if (!P4 || !P7 || P7.frame <= P4.frame) {
    return {
      phases: { P1, P4, P7 },
      shoulderWidthPx,
      pixelsPerMeter,
      handedness,
      metrics: emptyMeasurements(shoulderWidthPx).metrics,
      sequenceSeries: null,
    };
  }

  const n = frames.length;
  const leadShoulder = leadSide === "left" ? LM.L_SHOULDER : LM.R_SHOULDER;
  const leadWrist = leadSide === "left" ? LM.L_WRIST : LM.R_WRIST;
  const times = frames.map((f) => f.t_ms);

  // --- Full-range segment speeds (used for both peaks and the heatmap) ---

  // Pelvis & thorax axial rotation via foreshortening -> angular velocity.
  const pelvisRot = rotationDegSeries(frames, 0, n - 1, LM.L_HIP, LM.R_HIP, W);
  const thoraxRot = rotationDegSeries(
    frames,
    0,
    n - 1,
    LM.L_SHOULDER,
    LM.R_SHOULDER,
    W,
  );
  const pelvisVel = derivative(pelvisRot.deg, times).map((v) => Math.abs(v));
  const thoraxVel = derivative(thoraxRot.deg, times).map((v) => Math.abs(v));

  // Lead arm swings largely in the image plane, so in-plane angular velocity
  // is valid here.
  const armFull = angularVelocityFromLine(
    frames,
    0,
    n - 1,
    W,
    H,
    leadShoulder,
    leadWrist,
  );

  // Extrapolated clubhead position -> speed (m/s) and acceleration (m/s^2).
  const chx: number[] = [];
  const chy: number[] = [];
  let lastX = 0;
  let lastY = 0;
  for (let i = 0; i < n; i++) {
    const ch = extrapolateClubhead(
      frames[i].landmarks,
      leadSide,
      shoulderWidthPx,
      W,
      H,
    );
    if (ch) {
      lastX = ch.x * W;
      lastY = ch.y * H;
    }
    chx.push(lastX);
    chy.push(lastY);
  }
  const chxS = smooth(chx);
  const chyS = smooth(chy);
  const cvx = derivative(chxS, times);
  const cvy = derivative(chyS, times);
  // (px/ms) * (1000 ms/s) / (px/m) = m/s
  const clubSpeedMs = cvx.map(
    (v, i) => (Math.hypot(v, cvy[i]) * 1000) / (pixelsPerMeter || 1),
  );
  const clubAccel = derivative(clubSpeedMs, times).map((v) => Math.abs(v) * 1000);

  // --- Peaks over the downswing window P4 -> P7 ---
  const dStart = P4.frame;
  const dEnd = P7.frame;
  const dt = times.slice(dStart, dEnd + 1);
  const sl = (a: number[]) => a.slice(dStart, dEnd + 1);

  const pelvisPeakMs = peakTime(dt, sl(pelvisVel));
  const thoraxPeakMs = peakTime(dt, sl(thoraxVel));
  const armPeakMs = peakTime(dt, sl(armFull.angVel));
  const clubPeakMs = peakTime(dt, sl(clubSpeedMs).map((v) => Math.abs(v)));

  const segs: { name: "pelvis" | "thorax" | "arm" | "club"; t: number | null }[] = [
    { name: "pelvis", t: pelvisPeakMs },
    { name: "thorax", t: thoraxPeakMs },
    { name: "arm", t: armPeakMs },
    { name: "club", t: clubPeakMs },
  ];
  const haveAll = segs.every((s) => s.t !== null && Number.isFinite(s.t));
  const order = haveAll
    ? segs
        .slice()
        .sort((a, b) => (a.t as number) - (b.t as number))
        .map((s) => s.name)
    : [];
  const correct =
    haveAll &&
    order.length === 4 &&
    order[0] === "pelvis" &&
    order[1] === "thorax" &&
    order[2] === "arm" &&
    order[3] === "club";

  // Club speed + shaft-load proxy over the downswing.
  const clubSpeeds = sl(clubSpeedMs).map((v) => Math.abs(v));
  const peakSpeedMs = clubSpeeds.length ? Math.max(...clubSpeeds) : 0;
  const accelDown = sl(clubAccel);
  const peakAccel = accelDown.length ? Math.max(...accelDown) : 0;

  // Foreshortening rotation at the top of the backswing (P4).
  const pelvisRotationDegP4 = safeNum(pelvisRot.deg[P4.frame] ?? 0);
  const shoulderRotationDegP4 = safeNum(thoraxRot.deg[P4.frame] ?? 0);

  // Normalized per-frame speeds for the transfer heatmap.
  const normSeries = (a: number[]) => {
    let m = 0;
    for (const v of a) if (Number.isFinite(v) && v > m) m = v;
    return a.map((v) => (m > 0 ? safeNum(v / m) : 0));
  };
  const sequenceSeries: SequenceSeries = {
    t_ms: times,
    pelvis: normSeries(pelvisVel),
    thorax: normSeries(thoraxVel),
    arm: normSeries(armFull.angVel),
    club: normSeries(clubSpeedMs.map((v) => Math.abs(v))),
  };

  // 2. Lead hip displacement P4 -> P7
  const leadHipIdx = leadSide === "left" ? LM.L_HIP : LM.R_HIP;
  const lhP4 = frames[P4.frame].landmarks[leadHipIdx];
  const lhP7 = frames[P7.frame].landmarks[leadHipIdx];
  let dx_norm = 0;
  let dz_norm = 0;
  let earlyExtension = false;
  if (lhP4 && lhP7 && shoulderWidthPx > 0) {
    dx_norm = safeNum(((lhP7.x - lhP4.x) * W) / shoulderWidthPx);
    dz_norm = safeNum(((lhP7.z - lhP4.z) * W) / shoulderWidthPx);
    // In MediaPipe, smaller z = closer to camera. Hips moving toward the ball
    // (camera) = z decreasing. NOTE: single-camera z is unreliable; this is a
    // low-confidence proxy and is far better measured from down-the-line.
    earlyExtension = dz_norm < -SWING.earlyExtensionHipTowardBallSw;
  }

  // 3. Spine angle change P1 -> P7
  let spineAngleChangeDeg = 0;
  if (P1) {
    const aP1 = spineAngleDeg(frames[P1.frame]);
    const aP7 = spineAngleDeg(frames[P7.frame]);
    if (aP1 !== null && aP7 !== null) {
      spineAngleChangeDeg = safeNum(aP7 - aP1);
    }
  }

  // 4. Head sway P1 -> P4 (lateral)
  let headSwayNorm = 0;
  if (P1 && shoulderWidthPx > 0) {
    const hcP1 = headCenter(frames[P1.frame]);
    const hcP4 = headCenter(frames[P4.frame]);
    if (hcP1 && hcP4) {
      headSwayNorm = safeNum(((hcP4.x - hcP1.x) * W) / shoulderWidthPx);
    }
  }

  // Suppress unused-variable warnings for helpers exposed only for completeness.
  void stddev;
  void lmVis;
  void wristSpeed;

  return {
    phases: { P1, P4, P7 },
    shoulderWidthPx,
    pixelsPerMeter,
    handedness,
    metrics: {
      kinematicSequence: {
        pelvisPeakMs,
        thoraxPeakMs,
        armPeakMs,
        clubPeakMs,
        order,
        correct,
      },
      leadHipDisplacement: { dx_norm, dz_norm, earlyExtension },
      spineAngleChangeDeg,
      headSwayNorm,
      rotation: {
        pelvisRotationDegP4,
        shoulderRotationDegP4,
        xFactorProxyDeg: safeNum(shoulderRotationDegP4 - pelvisRotationDegP4),
      },
      club: {
        peakSpeedMs: safeNum(peakSpeedMs),
        peakSpeedMph: safeNum(peakSpeedMs * 2.23694),
        shaftLoadProxy: safeNum(peakAccel * CLUB.headMassKg),
      },
    },
    sequenceSeries,
  };
}
