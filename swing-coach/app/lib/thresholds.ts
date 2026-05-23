// Biomechanical tolerance bands and detection parameters for a real golf
// swing. Values are grounded in the literature cited by the system prompt:
// TPI fault-prevalence triggers, Cheetham et al. (kinematic sequence /
// X-factor), and the Bourgain et al. 2022 systematic review. Distances are
// normalized to the golfer's address-frame shoulder width (s_w) unless noted.
//
// These are the numbers that describe an actual swing — not values chosen to
// fit the detection code. Tune HERE, in one place, when calibrating against
// real swings.

export const SWING = {
  // --- Timing (seconds) ---
  // Tour backswing (P1 -> P4) averages ~0.75-0.85 s; downswing ~0.25 s.
  backswingDurationS: { min: 0.75, max: 0.85 },
  // Tempo = backswing : downswing time. Tour ~3:1.
  tempoRatioTarget: 3,

  // --- Address (P1) stillness detection ---
  // The address hold is brief and nearly motionless. Treat per-frame joint
  // motion below this fraction of s_w as "still".
  addressStillWindowS: 0.4,
  addressMotionThreshSw: 0.01, // s_w / frame

  // --- Fault triggers (from the prompt's TPI table) ---
  // Lead hip moves toward the ball P4 -> P7 by more than this = early extension.
  earlyExtensionHipTowardBallSw: 0.1,
  // Spine angle change P1 -> P7 beyond this (degrees) = loss of posture.
  lossOfPostureSpineDeltaDeg: 10,
  // Trail hip lateral shift P1 -> P4 beyond this = sway.
  swayTrailHipSw: 0.15,
  // Head (nose) lateral shift P1 -> P4 beyond this = sway-class fault.
  headSwaySw: 0.15,
  // Lead arm angle at impact below this (degrees) = chicken wing.
  chickenWingLeadArmDeg: 165,
} as const;

// Visibility floor below which a landmark is considered unreliable
// (the prompt asks us to exclude < 0.5 from peak detection).
export const MIN_VISIBILITY = 0.5;
