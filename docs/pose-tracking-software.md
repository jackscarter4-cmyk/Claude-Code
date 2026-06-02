# Pose-Tracking Software for Fine-Tuning Kinematic Accuracy

Research notes on what's out there beyond MediaPipe for improving the
kinematic tracking in this app. Tiered from "drop-in alternative" to
"enterprise-grade."

---

## Tier 1 — Open-source, you can use or fine-tune yourself

### MediaPipe Pose (what we have)
- Browser-native, real-time, runs offline on the user's device.
- Accuracy on golf swings: OKS ≈ **0.636** (paper: *On the Utility of Pose
  Estimation Models for Golf Swing Understanding*). Degrades on fast motion.
- Best choice for an **in-browser** prototype. The native v1 (Apple Watch +
  iOS) would use Apple Vision instead.

### RTMPose (the upgrade path for production)
- Best **accuracy/speed balance**: real-time (30+ FPS) on a modern GPU, with
  competitive accuracy to ViTPose. Designed for production deployment.
- Part of **MMPose** (OpenMMLab) — a full training/fine-tuning toolbox.
- Practical use here: train a **golf-specific** RTMPose on annotated golf
  swing clips → better landmark accuracy than generic MediaPipe.

### ViTPose / ViTPose++
- State-of-the-art on COCO / MPII / CrowdPose benchmarks (Vision Transformer
  backbone). Slower than RTMPose; needs a GPU.
- Use it to **generate ground-truth annotations** for fine-tuning a smaller
  model (RTMPose, or a distilled MediaPipe), OR run it server-side if latency
  isn't critical.

### **GolfPose (a real find for this project)**
- Research paper (ICPR 2024) — combines **HRNet + ViTPose + DEKR + MixSTE**
  specifically for **"golfer-club pose estimation"** — i.e., it tracks the
  body **and the club**, which is exactly the gap we have.
- Sample data and weights typically posted with these papers; reproducing
  their pipeline gives us a path to **real club tracking** instead of the
  motion-diff hack we're prototyping now.
- This is the most directly relevant academic work I found.

### Pose2Sim
- Open-source pipeline: **multiple camera 2D pose → 3D OpenSim model**.
- Worth it only if we add a second camera (face-on + DTL simultaneously).
  Goes well beyond what we need for v1 but unlocks true 3D kinematics.

### MMPose
- The OpenMMLab toolbox that hosts RTMPose, ViTPose, HRNet, etc. Pick model,
  point at a dataset (or annotate your own), train. This is **the** tool for
  fine-tuning.

---

## Tier 2 — Commercial markerless platforms with developer integration

### Theia3D — the real pro-grade option
- Markerless mocap purpose-built for biomechanics; tracks **124 keypoints**
  for 3D skeletal models.
- **Has a developer story**: command-line + library API for embedding into
  your own infrastructure with customizable outputs. Real integration path.
- Pricing is enterprise (no public list). The serious answer if budget allows
  and you want sports-grade accuracy without training models yourself.

### KinaTrax
- The "in-stadium" pro option (used at MLB venues). Multi-camera, very
  high-end. Almost certainly out of scope unless you're going pro-tier.

### Sportsbox AI
- Markerless **3D golf motion analysis from a single slow-mo video**, runs as
  an iOS app. Closest to what we're building, already shipped.
- I didn't find a public developer API — appears to be product-only. Worth
  contacting if you ever want to license their backend rather than build.

### Swing Catalyst, Onform
- Golf-specific markerless mocap platforms targeted at coaches and academies.
  Product-tier, not developer-API tier (as far as I can tell publicly).

---

## Tier 3 — Native-platform vendor SDKs

### Apple Vision (`VNDetectHumanBodyPoseRequest`)
- iOS 17+, on-device, free with the Apple stack. This is the right pose API
  for the native v1 build alongside the Apple Watch IMU.
- Reasonable accuracy out of the box; lower setup cost than running MediaPipe
  iOS, and improving every iOS release.

---

## Recommendation by goal

| If you want… | Use |
|---|---|
| **Better tracking in the browser** without a new stack | Stay on MediaPipe; tune `thresholds.ts` against real swings; treat as the prototype it is |
| **Real club tracking from a single video** | Reproduce **GolfPose** (HRNet + ViTPose + DEKR + MixSTE). Highest-leverage research lead. |
| **A custom golf-tuned pose model** | **MMPose + RTMPose**, fine-tuned on annotated golf clips |
| **3D from two cameras** without commercial software | **Pose2Sim** |
| **Sports-grade accuracy out of the box** with an integration API | **Theia3D** (paid, enterprise) |
| **Native iOS app** (the v1 path) | **Apple Vision** + Apple Watch high-frequency motion |

---

## Single highest-impact next step

For *this app's* accuracy ceiling, the standout is **GolfPose**: it directly
addresses our biggest gap (the club isn't tracked) and is academic-published,
so the architecture is documented. Reproducing it (or even just its
inference) would replace our motion-diff clubhead hack with a learned
detector — which is what would actually unlock honest path / shot-shape
metrics.

Sources:
- [GolfPose — ICPR 2024 paper](https://minghanlee.github.io/papers/ICPR_2024_GolfPose.pdf)
- [MMPose — OpenMMLab](https://mmpose.com/)
- [Pose2Sim — perfanalytics/pose2sim](https://github.com/perfanalytics/pose2sim)
- [Theia Markerless](https://www.theiamarkerless.com/)
- [Sportsbox AI](https://www.sportsbox.ai/)
- [Onform — Markerless 3D golf mocap](https://onform.com/blog/onform-launches-fast-reliable-and-accessible-markerless-3d-motion-capture-for-golf/)
- [Swing Catalyst Markerless](https://swingcatalyst.com/products/mocap)
- [Markerless 3D pose multi-view survey](https://arxiv.org/html/2407.03817v1)
- [Single-video markerless golf swing study (PMC)](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10684732/)
