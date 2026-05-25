# Native Watch Capture — Architecture Notes (v1)

This document describes how to add **watch-driven accelerometer capture** to the
native v1 app. It is intentionally NOT implemented in the web prototype, because
neither Apple Watch nor Garmin exposes raw IMU data to a web page. The web
prototype proves the analysis pipeline (pose → phases → metrics → Claude). This
doc specifies the capture layer that feeds that same pipeline in the native app.

## Why this can't live in the web app

- **Apple Watch** sensor data (CoreMotion) is only available to a native
  **watchOS app** with an **iOS companion app**. There is no browser/Web
  Bluetooth path to Apple Watch IMU.
- **Garmin** raw IMU requires a **Connect IQ** app (Monkey C) on the watch,
  communicating to a phone over BLE/ANT or the Connect IQ mobile SDK.
- The browser `DeviceMotion` API can read the **phone's own** accelerometer,
  but not a paired watch's. (That's a separate, web-buildable feature.)

So the watch loop is a native-app concern. The good news: it only has to produce
three event types and a coarse timestamp — all the real analysis already runs on
the pose time series.

---

## The capture loop (target behavior)

```
WATCH (IMU @ ~100 Hz)                         PHONE (on tripod)
  swing-start detector  ──SWING_START──▶  commit rolling 4s video buffer
  impact spike detector ──IMPACT──────▶  mark t_impact prior (P7 hint)
  1.5s after impact     ──SWING_END────▶  stop recording, run pipeline
        ▲                                          │
        └────────── haptic + 3-word verdict ◀──────┘
```

The phone keeps a **rolling 4-second video buffer** in memory at all times.
The watch trigger commits that buffer so the **address position is already
captured** (you can't capture address if you start recording on the trigger).

### Event protocol (local transport, no cloud round-trip)

```
SWING_START  { swing_id, t_watch_ms }
IMPACT       { swing_id, t_watch_ms, impact_g }   // P7 prior, not ground truth
SWING_END    { swing_id, t_watch_ms }
```

`t_watch_ms` is only a **coarse alignment**. The authoritative P1–P8 phase
detection still runs on the pose data (the same logic as `app/lib/metrics.ts`
in the prototype). The IMPACT event is a **prior** that disambiguates P7, not a
replacement for it.

---

## Apple Watch path

### Components
1. **watchOS app** (SwiftUI + CoreMotion)
   - `CMBatchedSensorManager` (Apple Watch Series 8+/Ultra, watchOS 10+) for
     high-rate batched accelerometer + gyro (~200 Hz), or `CMMotionManager`
     `startDeviceMotionUpdates` (~100 Hz) as the baseline.
   - Run inside a **`HKWorkoutSession`** so the app keeps sampling with the
     wrist down and the screen off (without a workout session, background
     sensor access is heavily throttled).
2. **iOS companion app**
   - Holds the camera + rolling buffer (AVFoundation, `AVCaptureSession`,
     ring buffer of `CMSampleBuffer`s ≈ 4 s).
   - Runs MediaPipe iOS (or Apple Vision `VNDetectHumanBodyPoseRequest` on
     iOS 17+) for on-device pose.
3. **Transport: `WatchConnectivity`**
   - `WCSession.sendMessage` for low-latency live events while both apps are
     foreground/reachable; `transferUserInfo` as the queued fallback.
   - Typical on-wrist→phone latency is tens of ms — fine for the 5 s budget.

### Swing-start detection (on watch)
The published IMU swing detectors hit ~96% recall with ~10% false positives on
practice motions. Practical detector:
- Band-pass the wrist acceleration; look for the **takeaway signature**:
  sustained rotation away from target + a rising acceleration envelope over
  ~150–300 ms, gated by a minimum gyro magnitude.
- To cut false positives, either (a) accept them and let phone-side pose
  verification discard non-swings, or (b) add a "hold to arm" gesture.

### Impact detection (on watch)
- Look for the sharp **z-axis jolt** (shock) above a calibrated threshold,
  within a plausible window after SWING_START. Record `impact_g`.
- Send IMPACT immediately; schedule SWING_END for +1.5 s.

---

## Garmin path

### Components
1. **Connect IQ app/data field** (Monkey C)
   - `Sensor.registerSensorDataListener` / the accelerometer API for raw
     samples (rate depends on device, commonly 25–100 Hz; high-rate modes on
     newer devices).
   - Same swing-start / impact logic as above, tuned to the available rate.
2. **Phone transport**
   - **Connect IQ Mobile SDK** (`ConnectIQ` iOS/Android) app-message channel
     between the watch app and your phone app, **or**
   - A custom **BLE GATT** service if you ship your own characteristics.
3. Phone side is identical to the Apple path (rolling buffer + on-device pose).

### Caveats
- Sample rate is more device-dependent than Apple; calibrate thresholds per
  model. Lower rates degrade impact-spike timing — lean harder on the pose-based
  P7 and treat `impact_g` timing as coarse.

---

## How watch events plug into the existing pipeline

The prototype already computes phases and metrics from pose. In the native app:

1. **Commit buffer on SWING_START** → you have frames covering address onward.
2. **Run pose** on the committed clip (on-device).
3. **Phase detection** runs exactly as in `metrics.ts`, with two upgrades:
   - Use `t_watch_ms` of SWING_START to bound the P1 search window.
   - Use the IMPACT event as a **P7 prior**: restrict the P7 (bottom-of-arc)
     search to a small window around `t_impact`. This directly fixes the
     "P7 drifts late" failure mode the prototype had to bound heuristically.
4. **Metrics + Claude diagnosis** are unchanged from the prototype.
5. **Feedback**: phone sends a 3-word verdict back to the watch (haptic pattern
   by fault category: 1 tap = clean, 2 = posture, 3 = sequencing).

### Latency budget (from the spec)
- Swing end → phone shows verdict: **≤ 5 s**.
- Watch haptic placeholder ("analyzing") at ~1.5 s, updated to verdict at ~5 s.
- Pose + metrics on-device ≈ tens of ms; the Claude call (~2–4 s) dominates.

---

## Calibration (one-time per setup)
- **Per-golfer IMU thresholds**: one calibration swing to set swing-start and
  impact-`g` thresholds to the user's tempo/strength.
- **Phone framing + reference s_w**: 3 s at address to record shoulder-width in
  pixels and address baselines (mirrors the prototype's address-frame s_w).

## Failure handling (from the spec)
- Watch loses Bluetooth → phone falls back to **motion detection on the video
  itself**; alert the user.
- Swing detected, no impact (whiff/practice) → tag as practice, don't analyze.
- Pose visibility below threshold → "reframe" haptic + on-screen indicator.

---

## Suggested build order
1. iOS companion: camera + 4 s rolling buffer + manual record button (no watch).
2. Port the prototype's phase/metric math to Swift (or run it in an embedded
   JS/RN layer first to reuse the existing TypeScript).
3. watchOS app: workout session + raw IMU streaming to phone (log only).
4. Swing-start detector on watch → commit buffer.
5. Impact detector → P7 prior wired into phase detection.
6. Verdict-to-watch haptics.
7. Garmin Connect IQ port once the Apple loop is proven.
