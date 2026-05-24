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
  valid: boolean; // false if we couldn't detect a full swing
};

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

/** 10 inside [lo,hi], fading linearly to 0 at the hard bounds. */
function scoreBand(
  v: number,
  lo: number,
  hi: number,
  hardLo: number,
  hardHi: number,
): number {
  if (v >= lo && v <= hi) return 10;
  if (v < lo) return clamp((10 * (v - hardLo)) / (lo - hardLo), 0, 10);
  return clamp((10 * (hardHi - v)) / (hardHi - hi), 0, 10);
}

/** 10 at 0, fading to 0 at the fault threshold (lower is better). */
function scoreLowerBetter(absVal: number, fault: number): number {
  return clamp(10 * (1 - absVal / fault), 0, 10);
}

function gradeLabel(overall: number): string {
  if (overall >= 8.5) return "Tour-like";
  if (overall >= 7) return "Strong";
  if (overall >= 5.5) return "Solid";
  if (overall >= 4) return "Developing";
  return "Needs work";
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
    return { factors: [], overall: 0, label: "No swing detected", valid: false };
  }

  const backswingS = (P4.t_ms - P1.t_ms) / 1000;
  const downswingS = Math.max(0.01, (P7.t_ms - P4.t_ms) / 1000);
  const tempoRatio = backswingS / downswingS;

  const factors: FactorScore[] = [];

  // Shared across angles.
  factors.push({
    key: "tempo",
    label: "Tempo",
    score: scoreBand(tempoRatio, 2.7, 3.3, 1.3, 4.7),
    value: `${tempoRatio.toFixed(1)}:1`,
    ideal: `${SWING.tempoRatioTarget}:1`,
    weight: 1,
  });
  factors.push({
    key: "backswing",
    label: "Backswing length",
    score: scoreBand(
      backswingS,
      SWING.backswingDurationS.min,
      SWING.backswingDurationS.max,
      0.4,
      1.3,
    ),
    value: `${backswingS.toFixed(2)}s`,
    ideal: `${SWING.backswingDurationS.min}-${SWING.backswingDurationS.max}s`,
    weight: 0.5,
  });

  const seqScore = m.metrics.kinematicSequence.correct
    ? 10
    : m.metrics.kinematicSequence.order.length === 4
      ? 5
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
  return { factors, overall, label: gradeLabel(overall), valid: true };
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
    return { factors: [], overall: 0, label: "No swing detected", valid: false };
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
  return { factors, overall, label: gradeLabel(overall), valid: true };
}
