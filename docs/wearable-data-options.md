# Wearable IMU Data Options — Research Notes

Goal: get more accurate swing data than 2D phone-pose can give us, by adding a
wrist-worn IMU. Three candidates the user asked about: **Oura Ring**, **Apple
Watch**, **Garmin**.

---

## Oura Ring — NOT viable

- Oura API v2 (the only available integration) exposes **processed health
  metrics** only: sleep, readiness, activity summaries, HR / HRV time series,
  workouts, events. There is **no raw accelerometer / IMU stream** for
  third-party apps.
- Even with workarounds, a **finger-worn ring** is the wrong place to capture a
  golf swing. The wrist is where the literature centers (and where Apple
  Watch's high-frequency API lives).
- Verdict: **don't pursue Oura for swing capture.** Great recovery device, wrong
  tool for this job.

Sources:
- [The Oura API — Oura Member Care](https://support.ouraring.com/hc/en-us/articles/4415266939155-The-Oura-API)
- [Oura API v2 docs](https://cloud.ouraring.com/v2/docs)

---

## Apple Watch — best fit

The clearest path because Apple shipped APIs explicitly aimed at this exact
problem.

- **watchOS 10+ High-Frequency Motion API**: up to **200 Hz** accelerometer +
  gyro (~2× CoreMotion's baseline) — designed for fast/sudden motions like a
  golf swing.
- **Existing precedent**: **Golfshot Swing ID** runs on Apple Watch using this
  API to detect impact and compute tempo, rhythm, backswing, transition, and
  wrist-path metrics. So Apple itself markets it as the golf-swing platform.
- **Native build only**: requires a watchOS app + iOS companion in Swift —
  cannot be reached from a web app. Use `CMBatchedSensorManager` (Series 8+ /
  Ultra) for the highest rate, or `CMMotionManager` as the baseline.
- **Keep sampling with screen off / on tripod**: run the watch app inside an
  `HKWorkoutSession` (Apple throttles background sensor access without one).
- **Transport**: `WatchConnectivity` (`sendMessage` for low-latency live
  events; `transferUserInfo` queued fallback) to the iOS companion that holds
  the camera's rolling buffer.

Verdict: **build Apple Watch first.** Highest sample rate, built-for-purpose
API, existing reference implementation (Golfshot). Same architecture as the
`native-watch-capture.md` doc — just upgrade the IMU layer to the high-frequency
API on watchOS 10+.

Sources:
- [Apple — Apple Watch is the perfect golfing companion](https://www.apple.com/newsroom/2024/05/apple-watch-is-the-perfect-golfing-companion/)
- [Golfshot — Gen 2 Swing ID Metrics on Apple Watch](https://golfshot.com/blog/coming-soon-to-your-apple-watch-new-gen-2-swing-id-metrics)
- [Wrist-worn single inertial sensor golf-swing study (PMC)](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11035581/)

---

## Garmin — viable on modern devices, weaker than Apple

- **Connect IQ Sensor API** can register a callback for accelerometer samples,
  but the **rate is device-specific** and cached from the OS:
  - Old devices (e.g. Vivoactive): ~**1 Hz** — not usable for a swing.
  - Fenix-class: **~10 Hz+**, with higher rates on newer hardware.
- Fenix 5+ ships **built-in auto swing detection** the user could potentially
  trigger off of, but that's coarse compared to streaming raw IMU.
- **Transport**: Connect IQ Mobile SDK on iOS/Android, or a custom BLE GATT
  service if you want full control.
- **Realistic role**: a secondary platform after Apple Watch is proven. If the
  golfer wears a modern Garmin, support it via Connect IQ; otherwise direct
  them to Apple Watch for best accuracy.

Sources:
- [Connect IQ SDK — Garmin Developers](https://developer.garmin.com/connect-iq/)
- [Connect IQ Sensors — Core Topics](https://developer.garmin.com/connect-iq/core-topics/sensors/)
- [Garmin Forums — Accelerometer Sample Rate](https://forums.garmin.com/forum/developers/connect-iq/127110-)
- [Garmin Forums — Golf swing detection using accelerometers](https://forums.garmin.com/developer/connect-iq/f/discussion/7639/golf-swing-detection-using-accelerometers---any-suggestions)

---

## Recommended stack

1. **Phase 1 (now)**: keep tuning the camera-pose prototype. The grading brain
   is the durable part.
2. **Phase 2 (native v1)**: Apple Watch + iOS, using the watchOS 10
   High-Frequency Motion API. Three events: `SWING_START` (motion onset),
   `IMPACT` (z-axis jolt — used as a **P7 prior** for pose phase detection),
   `SWING_END` (~1.5 s after impact). Pipeline matches
   `native-v1-roadmap.md` and `native-watch-capture.md`.
3. **Phase 3 (broader)**: add Garmin via Connect IQ for users who don't have
   an Apple Watch. Skip Oura entirely.

The single biggest accuracy win this would buy us: **`IMPACT` as a P7 prior**
removes the heuristic guessing in `metrics.ts` (the bound-the-search-window
hack we have for the camera). Everything else in our scoring (rotation,
strike, hand sway) gets cleaner because phases land where they actually are.
