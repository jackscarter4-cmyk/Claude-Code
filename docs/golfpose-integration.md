# GolfPose Integration Plan

Status check after deep-diving the **GolfPose** project (Lee et al., ICPR 2024)
and assessing realistic integration with this web prototype.

---

## What GolfPose actually is

The repo (https://github.com/MingHanLee/GolfPose) releases an ensemble of
models fine-tuned on a custom Golf Swing dataset. It includes **18
checkpoints** across three layers:

- **Detectors** (object detection — locate the golfer and the club):
  - `golfpose_detector_1cls_faster_rcnn.pth` — golfer-with-club, AP 0.918
  - `golfpose_detector_1cls_yolox_s.pth` — golfer-with-club, **AP 0.984**
  - `golfpose_detector_2cls_faster_rcnn.pth` — golfer + club (2 classes), AP 0.884
  - `golfpose_detector_2cls_yolox_s.pth` — golfer + club, **AP 0.916** ⭐
- **2D Pose** (keypoints on golfer and club): HRNet-w48, ViTPose-H, DEKR
  variants × {golfer, club, combined} = 9 checkpoints.
- **3D Pose**: MixSTE-based, lifting 2D → 3D. 17–22 keypoints, error ~35–39 mm.

Framework: MMDetection + MMPose + PyTorch. CUDA GPU required for the ensemble.
License: not stated in the README; weights are released **on email request** to
the author (`mhlee.cs09@nycu.edu.tw`). Research-friendly; commercial use is
ambiguous and worth confirming.

---

## What's realistic to use here

### The full ensemble: **server-side only**
ViTPose-H + HRNet-w48 + MixSTE + MMDetection is too heavy for the browser
(hundreds of MB combined, GPU expected). Running the full pipeline means
standing up a Python GPU service (Modal / Replicate / RunPod / self-host).
Upload happens, video goes to a server, server returns keypoints + 3D pose +
club track. Best accuracy, but: latency, server cost, and the user's swing
video leaves the device.

### The single biggest win we can keep in the browser: **YOLOX-s 2-class detector** ⭐
- The `golfpose_detector_2cls_yolox_s.pth` model is a **golf-trained YOLOX-s
  that outputs golfer AND club bounding boxes per frame** (AP 0.916).
- YOLOX-s is small (~9M params, ~36 MB) and fast. Comparable to MediaPipe.
- It can be **exported to ONNX** (standard YOLOX path) and run client-side
  with **ONNX Runtime Web** (WebGPU/WASM backend). No backend needed.
- This is the genuine **club-tracking unlock** without abandoning the
  client-only architecture: replaces our motion-diff hack with a learned
  detector that actually knows what a golf club looks like.

### Hybrid (later)
Optional "deep analysis" button that uploads to a Python GPU service for the
full ensemble (3D pose, full biomechanics). For when you want lab-grade
numbers on a specific clip. Not v1.

---

## Concrete plan for in-browser club tracking

1. **Email Ming-Han Lee** at `mhlee.cs09@nycu.edu.tw` requesting the
   `golfpose_detector_2cls_yolox_s.pth` weights for evaluation. Confirm
   licensing (commercial vs research) — important if this app is going public.
2. **Convert PyTorch → ONNX** on a Linux + GPU box:
   - `torch.onnx.export` from the YOLOX-s model with the GolfPose weights.
   - Verify outputs match PyTorch reference on a few sample frames.
3. **Add ONNX Runtime Web** to the app:
   - `npm i onnxruntime-web` (~2 MB; ships its own wasm/webgpu bundles).
   - Load the converted model from a CDN or `public/` at first analysis.
4. **Per-frame detection in the analysis tick**:
   - Pre-process: downscale the current video frame to YOLOX-s input size
     (typically 640×640), normalize, NCHW float32 tensor.
   - Run inference, NMS, pick highest-confidence box of class "club".
   - Map box center back to normalized image coords; store on `Frame.club`.
   - Fall back to the existing motion-diff `detectClubhead` when no detection
     (e.g., occlusion).
5. **Draw it** (already wired) — the blue dot from our `drawSkeleton`
   becomes the learned detection. Visually verify it tracks the actual
   clubhead through the swing.
6. **Then** build path/shape on top: clubhead 2D trajectory through impact
   → in-to-out vs out-to-in path proxy; trajectory slope → attack proxy.

### Honest effort estimate
- Without a Mac/GPU/PyTorch handy: I can write the **TypeScript integration
  scaffold** (ONNX Runtime Web wrapper, pre/post-processing, draw glue) here
  — but the **PyTorch → ONNX conversion has to happen on a machine with PyTorch
  + a GPU**. I can't verify it in this Linux container without that.
- Total: ~1–2 days of work for someone with the conversion done; integration
  is straightforward once the `.onnx` file exists.

---

## Single-step recommendation

Send the email to request the weights and confirm licensing. While that's in
flight, I can write the in-browser integration scaffold against a placeholder
ONNX file, so the moment the model is in hand we can drop it in and see if
the blue dot actually follows the clubhead.
