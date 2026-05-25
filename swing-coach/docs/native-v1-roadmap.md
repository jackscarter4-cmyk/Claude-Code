# Native v1 — Build Roadmap (iOS + Apple Watch)

This is the plan for turning the web prototype into a native iOS app you can
ship on the App Store. The prototype already proves the **brain** — pose →
phase detection → metrics → scorecard. v1 keeps that logic and rebuilds the
shell natively so it can use the camera, the Apple Watch, and the App Store.

> Build environment: this requires a **Mac with Xcode**. It cannot be built in
> the Linux web container. Everything below is written so it can be implemented
> directly in an Xcode project.

---

## Stack

| Concern | Choice |
|---|---|
| Language / UI | Swift + SwiftUI |
| Camera + rolling buffer | AVFoundation (`AVCaptureSession`, `AVAssetWriter`) |
| On-device pose | **Apple Vision** `VNDetectHumanBodyPoseRequest` (iOS 17+) or MediaPipe Tasks iOS |
| Phase / metrics / grading | Swift port of `metrics.ts` + `grade.ts` (pure functions) |
| Watch capture | watchOS app (CoreMotion) + `WatchConnectivity` — see `native-watch-capture.md` |
| Local storage | SwiftData or Core Data (swings, measurements, grades) |
| Cloud sync (optional v1.1) | CloudKit (zero-backend) or Supabase |
| Distribution | TestFlight → App Store |

Why Apple Vision for pose: zero dependency, fully on-device, fast, and it maps
cleanly to the same joints we use (wrists, shoulders, hips, ankles). MediaPipe
iOS is the fallback if we want identical landmarks to the prototype.

---

## Architecture

```
AVCaptureSession (rolling 4s buffer)
        │  commit on trigger (watch IMU or manual button)
        ▼
Clip (CMSampleBuffers)  ──▶  Pose (Vision/MediaPipe, on-device)
        │                          │  [{t_ms, landmarks}]
        │                          ▼
        │                   PhaseDetector  (port of detectPhases)
        │                          ▼
        │                   Metrics        (port of computeMeasurements)
        │                          ▼
        │                   Grader         (port of grade.ts)
        ▼                          ▼
   SwiftData  ◀───────────  Swing { phases, measurements, grade }
        │
        ▼
   SwiftUI screens (Practice / Detail / Session / Progress)
```

---

## Porting the brain (the highest-value, lowest-risk work)

These prototype files are **pure functions over a keypoint array** — no DOM, no
React. They translate almost 1:1 to Swift structs + functions:

| Prototype (TS) | Swift target | Notes |
|---|---|---|
| `app/lib/metrics.ts` | `SwingMetrics.swift` | `detectPhases`, `computeMeasurements`, foreshortening rotation, clubhead extrapolation, impact proxies |
| `app/lib/grade.ts` | `SwingGrader.swift` | factor scoring + `combineGrades` |
| `app/lib/thresholds.ts` | `Thresholds.swift` | the tuning constants — keep as one file |

Porting checklist:
- Replace the MediaPipe `NormalizedLandmark[]` with a `[Joint]` where
  `Joint = (x: Double, y: Double, z: Double, visibility: Double)`, normalized
  0..1 with origin top-left (Vision uses bottom-left origin — **flip y** on
  ingest so the math is unchanged).
- Savitzky-Golay: use a small Swift SG implementation or a centered moving
  average (the prototype already falls back to a moving average for short
  arrays).
- Keep the joint-index map identical (nose 0, wrists 15/16, hips 23/24,
  ankles 27/28, etc. — Vision names map to these).
- Port the unit tests idea: run the same clip through web and native, compare
  phase frames and factor scores. They should match within rounding.

Because these are pure functions, you can validate the Swift port against the
TypeScript by feeding both the **same exported keypoint JSON** from the
prototype and diffing the outputs.

---

## Screens (from `swing_coach_spec.md`, v1 subset)

1. **Practice Mode** — calm dashboard: session swing count, last-swing 1-word
   status, dim live camera preview, last-5-swings strip, big manual record
   button.
2. **Single Swing Detail** — video + skeletal overlay + phase pins; the
   scorecard (overall /10 + per-factor bars); "Focus next" cue; expandable raw
   measurements.
3. **Session Review** — fault/score distribution across the session, best
   swing, trend, one takeaway line.
4. **Progress** — score + key factors over 7/30/90 days.
5. **Setup / Calibration** — framing helper, 3s address to capture reference
   shoulder width (s_w), one swing to calibrate watch IMU thresholds.

v1 can ship with just **Practice + Detail** and add the rest after.

---

## App Store specifics

- **Capabilities / entitlements**: Camera; (watch) HealthKit workout session
  for background sensor access; App Groups + WatchConnectivity for phone↔watch.
- **Privacy usage strings** (Info.plist) — required or the app is rejected:
  - `NSCameraUsageDescription` — "Records your golf swing for on-device
    analysis."
  - `NSMotionUsageDescription` (watch) — "Detects your swing to trigger
    recording."
  - If any health/workout: `NSHealthShareUsageDescription` etc.
- **On-device by default**: keep pose + analysis on-device; if you add cloud
  sync, disclose it in the privacy nutrition label.
- **Review risk**: a camera+analysis app with real native functionality is
  fine. (A thin web-view wrapper is the thing Apple rejects — that's Path A,
  which we are NOT doing.)
- **Distribution**: Apple Developer Program ($99/yr) → TestFlight for beta →
  App Store submission. Budget a review round or two.

---

## Suggested build order (milestones)

1. **Xcode project** + camera preview + manual record to a file.
2. **Vision pose** on a recorded clip → overlay skeleton (parity with the
   prototype's visual check).
3. **Port `thresholds` + `metrics`** → phase pins on the timeline.
4. **Port `grade`** → scorecard screen. Validate against the web outputs.
5. **SwiftData** persistence + saved-swings list (mirror the prototype).
6. **Rolling buffer** + manual trigger (capture address).
7. **watchOS app**: IMU streaming → `SWING_START`/`IMPACT`/`SWING_END`
   (see `native-watch-capture.md`); wire IMPACT as the P7 prior.
8. **Session + Progress** screens.
9. **TestFlight**, dogfood on real range sessions, tune `Thresholds.swift`.
10. **App Store** submission.

---

## What carries over vs. what's new

- **Carries over unchanged (the hard-won part):** phase detection logic, all
  metric formulas, the foreshortening rotation, the impact proxies, the scoring
  bands and weights. This is the bulk of the "is it any good" risk, already
  retired in the prototype.
- **New native work:** camera/buffer, Vision pose plumbing, SwiftUI screens,
  persistence, the watch app, and App Store packaging.

Keep iterating scoring in the **web prototype** (free, instant) until it feels
trustworthy; then port the final `thresholds`/`grade` values into Swift so the
native app launches already tuned.
