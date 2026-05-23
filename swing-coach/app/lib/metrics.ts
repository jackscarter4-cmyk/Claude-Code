import savitzkyGolay from "ml-savitzky-golay";
import type { Frame } from "./db";
import { SWING } from "./thresholds";

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

export type Measurements = {
  phases: Phases;
  shoulderWidthPx: number;
  handedness: "right" | "left";
  metrics: {
    kinematicSequence: KinematicSequence;
    leadHipDisplacement: LeadHipDisplacement;
    spineAngleChangeDeg: number;
    headSwayNorm: number;
  };
};

function emptyMeasurements(shoulderWidthPx = 0): Measurements {
  return {
    phases: { P1: null, P4: null, P7: null },
    shoulderWidthPx,
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
    },
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

/** P1: first contiguous window (>=0.4s) of low motion across major joints. */
function detectP1(
  frames: Frame[],
  W: number,
  H: number,
  shoulderWidthPx: number,
  leadSide: "left" | "right",
): { frame: number; t_ms: number } | null {
  if (frames.length < 5 || shoulderWidthPx <= 0) return null;
  const wristIdx = leadSide === "left" ? LM.L_WRIST : LM.R_WRIST;
  const trackedIdx = [
    wristIdx,
    LM.L_SHOULDER,
    LM.R_SHOULDER,
    LM.L_HIP,
    LM.R_HIP,
  ];
  // Per-frame velocity magnitude (px) for each tracked joint, averaged.
  const motion = new Array<number>(frames.length).fill(0);
  for (let i = 1; i < frames.length; i++) {
    let sum = 0;
    let n = 0;
    for (const idx of trackedIdx) {
      const a = frames[i - 1].landmarks[idx];
      const b = frames[i].landmarks[idx];
      if (!a || !b) continue;
      const dx = (b.x - a.x) * W;
      const dy = (b.y - a.y) * H;
      sum += Math.hypot(dx, dy);
      n++;
    }
    motion[i] = n === 0 ? Infinity : sum / n;
  }

  // Approximate frame rate from time deltas.
  const totalMs = frames[frames.length - 1].t_ms - frames[0].t_ms;
  const fps = totalMs > 0 ? ((frames.length - 1) * 1000) / totalMs : 30;
  const windowLen = Math.max(3, Math.round(fps * SWING.addressStillWindowS));
  // Low-motion threshold: a real address is nearly still.
  const threshold = SWING.addressMotionThreshSw * shoulderWidthPx;

  let bestStart = -1;
  let bestEnd = -1;
  let bestMean = Infinity;
  for (let i = 1; i + windowLen <= frames.length; i++) {
    const slice = motion.slice(i, i + windowLen);
    const mean = slice.reduce((a, b) => a + b, 0) / slice.length;
    if (mean < threshold && mean < bestMean) {
      bestStart = i;
      bestEnd = i + windowLen - 1;
      bestMean = mean;
      // Take the first qualifying window; break.
      break;
    }
  }

  if (bestStart < 0) {
    // Fall back: first frame with lowest motion in first 30% of video
    const cutoff = Math.max(5, Math.floor(frames.length * 0.3));
    let minIdx = 0;
    let minVal = Infinity;
    for (let i = 1; i < cutoff; i++) {
      if (motion[i] < minVal) {
        minVal = motion[i];
        minIdx = i;
      }
    }
    return { frame: minIdx, t_ms: frames[minIdx].t_ms };
  }
  const mid = Math.floor((bestStart + bestEnd) / 2);
  return { frame: mid, t_ms: frames[mid].t_ms };
}

/** P4: smoothed lead-wrist y minimum (highest) after P1. */
function detectP4(
  frames: Frame[],
  startFrame: number,
  leadSide: "left" | "right",
): { frame: number; t_ms: number } | null {
  if (startFrame >= frames.length - 1) return null;
  const wristIdx = leadSide === "left" ? LM.L_WRIST : LM.R_WRIST;
  const ys: number[] = [];
  for (let i = startFrame; i < frames.length; i++) {
    const lm = frames[i].landmarks[wristIdx];
    ys.push(lm ? lm.y : 1);
  }
  if (ys.length < 3) return null;
  const ysSmooth = smooth(ys);
  let minIdx = 0;
  let minVal = Infinity;
  for (let i = 0; i < ysSmooth.length; i++) {
    if (ysSmooth[i] < minVal) {
      minVal = ysSmooth[i];
      minIdx = i;
    }
  }
  const frame = startFrame + minIdx;
  return { frame, t_ms: frames[frame].t_ms };
}

/**
 * P7 (impact): lowest point of the lead-hand arc after the top.
 *
 * In a face-on view the hands descend from P4, reach their lowest point at
 * impact (largest y, since y increases downward), then rise again into the
 * follow-through. The old rule used peak horizontal wrist speed, which peaks
 * in the follow-through and pushed P7 too late. The bottom of the arc is a
 * robust 2D impact proxy for the hands.
 */
function detectP7(
  frames: Frame[],
  startFrame: number,
  leadSide: "left" | "right",
): { frame: number; t_ms: number } | null {
  if (startFrame >= frames.length - 1) return null;
  const wristIdx = leadSide === "left" ? LM.L_WRIST : LM.R_WRIST;
  const ys: number[] = [];
  for (let i = startFrame; i < frames.length; i++) {
    const lm = frames[i].landmarks[wristIdx];
    ys.push(lm ? lm.y : i > 0 ? ys[i - 1] : 0);
  }
  if (ys.length < 3) return null;
  const ysSmooth = smooth(ys);
  // Largest y after the top = lowest hands = impact.
  let maxIdx = 0;
  let maxVal = -Infinity;
  for (let i = 0; i < ysSmooth.length; i++) {
    if (ysSmooth[i] > maxVal) {
      maxVal = ysSmooth[i];
      maxIdx = i;
    }
  }
  const frame = startFrame + maxIdx;
  return { frame, t_ms: frames[frame].t_ms };
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

  // Phase detection
  const P1 = detectP1(frames, W, H, shoulderWidthPx, leadSide);

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

  const P1Frame = P1?.frame ?? 0;
  const P4 = detectP4(frames, P1Frame + 1, leadSide);
  const P7 = P4 ? detectP7(frames, P4.frame + 1, leadSide) : null;

  // If we couldn't determine P4 or P7 reliably, return what we have.
  if (!P4 || !P7 || P7.frame <= P4.frame) {
    return {
      phases: { P1, P4, P7 },
      shoulderWidthPx,
      handedness,
      metrics: emptyMeasurements(shoulderWidthPx).metrics,
    };
  }

  // 1. Kinematic sequence over P4..P7 downswing.
  const leadShoulder = leadSide === "left" ? LM.L_SHOULDER : LM.R_SHOULDER;
  const leadWrist = leadSide === "left" ? LM.L_WRIST : LM.R_WRIST;

  const pelvis = angularVelocityFromLine(
    frames,
    P4.frame,
    P7.frame,
    W,
    H,
    LM.L_HIP,
    LM.R_HIP,
  );
  const thorax = angularVelocityFromLine(
    frames,
    P4.frame,
    P7.frame,
    W,
    H,
    LM.L_SHOULDER,
    LM.R_SHOULDER,
  );
  const arm = angularVelocityFromLine(
    frames,
    P4.frame,
    P7.frame,
    W,
    H,
    leadShoulder,
    leadWrist,
  );
  const club = wristSpeed(frames, P4.frame, P7.frame, W, H, leadWrist);

  const pelvisPeakMs = peakTime(pelvis.times, pelvis.angVel);
  const thoraxPeakMs = peakTime(thorax.times, thorax.angVel);
  const armPeakMs = peakTime(arm.times, arm.angVel);
  const clubPeakMs = peakTime(club.times, club.speed);

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

  return {
    phases: { P1, P4, P7 },
    shoulderWidthPx,
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
    },
  };
}
