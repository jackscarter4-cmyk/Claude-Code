import type { CameraAngle } from "./db";
import type { Measurements } from "./metrics";
import { SWING } from "./thresholds";

export type FactorScore = {
  key: string;
  label: string;
  score: number; // 0..10
  value: string; // formatted measured value
  ideal: string; // human-readable target
  weight: number;
};

export type SwingGrade = {
  factors: FactorScore[];
  overall: number; // 0..10, weighted average of factors
  label: string;
  focus: string | null; // the factor most worth improving
  valid: boolean; // false if we couldn't detect a full swing
};

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

// Nothing scores below this — the point is to encourage improvement, not to
// hand out demoralizing zeros for an amateur swing.
const FLOOR = 4;

/**
 * Encouraging band score: full marks inside [lo,hi], then gentle partial
 * credit out to the hard bounds (and never below FLOOR).
 */
function scoreBand(
  v: number,
  lo: number,
  hi: number,
  hardLo: number,
  hardHi: number,
): number {
  if (v >= lo && v <= hi) return 10;
  if (v < lo) {
    return clamp(10 - (10 - FLOOR) * ((lo - v) / (lo - hardLo)), FLOOR, 10);
  }
  return clamp(10 - (10 - FLOOR) * ((v - hi) / (hardHi - hi)), FLOOR, 10);
}

/**
 * Encouraging "lower is better" score. Full marks while comfortably under the
 * fault line, ~6.5 at the fault line itself, easing to FLOOR beyond it.
 */
function scoreLowerBetter(absVal: number, fault: number): number {
  const good = 0.5 * fault; // at or under this = clean
  if (absVal <= good) return 10;
  if (absVal <= fault) {
    return 10 - 3.5 * ((absVal - good) / (fault - good)); // 10 -> 6.5
  }
  return clamp(6.5 - (6.5 - FLOOR) * ((absVal - fault) / fault), FLOOR, 6.5);
}

function gradeLabel(overall: number): string {
  if (overall >= 8.5) return "Tour-like";
  if (overall >= 7) return "Dialed in";
  if (overall >= 5.5) return "On track";
  if (overall >= 4.5) return "Coming along";
  return "Building blocks";
}

/**
 * Score the factors a given camera angle can actually measure, each out of 10
 * against the published ideal bands. Like a looksmax-style per-feature score,
 * then averaged into one overall. Angle-aware: face-on scores turn/sway/tempo;
 * down-the-line scores posture/early-extension (which need depth).
 */
export function gradeSwing(m: Measurements, angle: CameraAngle): SwingGrade {
  const { P1, P4, P7 } = m.phases;
  if (!P1 || !P4 || !P7) {
    return {
      factors: [],
      overall: 0,
      label: "No swing detected",
      focus: null,
      valid: false,
    };
  }

  const backswingS = (P4.t_ms - P1.t_ms) / 1000;
  const downswingS = Math.max(0.01, (P7.t_ms - P4.t_ms) / 1000);
  const tempoRatio = backswingS / downswingS;

  const factors: FactorScore[] = [];

  // Strike factors — how the ball gets struck — are the focus, so they carry
  // the most weight. Low point relative to stance = ball-first vs fat.
  factors.push({
    key: "lowpoint",
    label: "Strike / low point",
    score: scoreBand(m.metrics.impact.lowPointNorm, 0.0, 0.35, -0.5, 0.8),
    value: `${m.metrics.impact.lowPointNorm.toFixed(2)} s_w ${
      m.metrics.impact.lowPointNorm >= 0 ? "ahead" : "behind"
    }`,
    ideal: "at / just ahead of center",
    weight: 2,
  });
  factors.push({
    key: "shaftlean",
    label: "Shaft lean (proxy)",
    score: scoreBand(m.metrics.impact.shaftLeanNorm, 0.05, 0.45, -0.4, 0.8),
    value: `${m.metrics.impact.shaftLeanNorm.toFixed(2)} s_w`,
    ideal: "hands ahead",
    weight: 1.5,
  });

  // Tempo & backswing length vary a lot by player — keep them as low-weight
  // "style" factors with wide tolerances so they barely move the overall.
  factors.push({
    key: "tempo",
    label: "Tempo (style)",
    score: scoreBand(tempoRatio, 2.2, 3.8, 1.0, 5.5),
    value: `${tempoRatio.toFixed(1)}:1`,
    ideal: `~${SWING.tempoRatioTarget}:1`,
    weight: 0.25,
  });
  factors.push({
    key: "backswing",
    label: "Backswing length (style)",
    score: scoreBand(backswingS, 0.55, 1.05, 0.3, 1.7),
    value: `${backswingS.toFixed(2)}s`,
    ideal: "varies by player",
    weight: 0.25,
  });

  const seqScore = m.metrics.kinematicSequence.correct
    ? 10
    : m.metrics.kinematicSequence.order.length === 4
      ? 6.5
      : null;
  if (seqScore !== null) {
    factors.push({
      key: "sequence",
      label: "Kinematic sequence",
      score: seqScore,
      value: m.metrics.kinematicSequence.order.join(" → ") || "—",
      ideal: "pelvis → thorax → arm → club",
      weight: 1,
    });
  }

  if (angle === "face_on") {
    factors.push({
      key: "head",
      label: "Head stability",
      score: scoreLowerBetter(
        Math.abs(m.metrics.headSwayNorm),
        SWING.headSwaySw,
      ),
      value: `${m.metrics.headSwayNorm.toFixed(2)} s_w sway`,
      ideal: `< ${SWING.headSwaySw} s_w`,
      weight: 1,
    });
    factors.push({
      key: "turn",
      label: "Shoulder turn",
      score: scoreBand(m.metrics.rotation.shoulderRotationDegP4, 75, 95, 35, 95),
      value: `${m.metrics.rotation.shoulderRotationDegP4.toFixed(0)}°`,
      ideal: "≈ 90°",
      weight: 1,
    });
    factors.push({
      key: "xfactor",
      label: "X-factor (proxy)",
      score: scoreBand(m.metrics.rotation.xFactorProxyDeg, 25, 45, 5, 65),
      value: `${m.metrics.rotation.xFactorProxyDeg.toFixed(0)}°`,
      ideal: "25-45°",
      weight: 1,
    });
  }

  if (angle === "down_the_line") {
    factors.push({
      key: "posture",
      label: "Loss of posture",
      score: scoreLowerBetter(
        Math.abs(m.metrics.spineAngleChangeDeg),
        SWING.lossOfPostureSpineDeltaDeg,
      ),
      value: `${m.metrics.spineAngleChangeDeg.toFixed(1)}° change`,
      ideal: `< ${SWING.lossOfPostureSpineDeltaDeg}°`,
      weight: 1.5,
    });
    factors.push({
      key: "earlyext",
      label: "Early extension",
      score: scoreLowerBetter(
        Math.abs(m.metrics.leadHipDisplacement.dz_norm),
        SWING.earlyExtensionHipTowardBallSw,
      ),
      value: `${m.metrics.leadHipDisplacement.dz_norm.toFixed(2)} s_w`,
      ideal: `< ${SWING.earlyExtensionHipTowardBallSw} s_w`,
      weight: 1.5,
    });
  }

  const overall = weightedAverage(factors);
  return {
    factors,
    overall,
    label: gradeLabel(overall),
    focus: weakestFactor(factors),
    valid: true,
  };
}

function weakestFactor(factors: FactorScore[]): string | null {
  let weakest: FactorScore | null = null;
  for (const f of factors) {
    if (f.score >= 8) continue; // already good — not a focus
    if (!weakest || f.score < weakest.score) weakest = f;
  }
  return weakest?.label ?? null;
}

function weightedAverage(factors: FactorScore[]): number {
  const totalW = factors.reduce((a, f) => a + f.weight, 0);
  if (totalW === 0) return 0;
  return factors.reduce((a, f) => a + f.score * f.weight, 0) / totalW;
}

/**
 * Merge grades from several swings (e.g. one face-on + one down-the-line) into
 * one scorecard. Each factor key is averaged across whatever swings measured
 * it, so the combined card is more complete than any single angle.
 */
export function combineGrades(grades: SwingGrade[]): SwingGrade {
  const valid = grades.filter((g) => g.valid);
  if (valid.length === 0) {
    return {
      factors: [],
      overall: 0,
      label: "No swing detected",
      focus: null,
      valid: false,
    };
  }
  const byKey = new Map<string, FactorScore[]>();
  for (const g of valid) {
    for (const f of g.factors) {
      const arr = byKey.get(f.key) ?? [];
      arr.push(f);
      byKey.set(f.key, arr);
    }
  }
  const factors: FactorScore[] = [];
  for (const arr of byKey.values()) {
    const score = arr.reduce((a, f) => a + f.score, 0) / arr.length;
    factors.push({ ...arr[0], score });
  }
  const overall = weightedAverage(factors);
  return {
    factors,
    overall,
    label: gradeLabel(overall),
    focus: weakestFactor(factors),
    valid: true,
  };
}
