# Models

Drop `golfpose_yolox_s.onnx` here to enable learned club tracking.

How to produce it:

1. Download the GolfPose YOLOX-s (person+club) checkpoint — public link, no
   auth needed: http://gofile.me/4RvCV/ALgsmvtPw
   (from https://github.com/MingHanLee/GolfPose)
2. Save it as `models/golfpose_detector_2cls_yolox_s.pth` in the repo root.
3. Run `python scripts/export_golfpose_onnx.py` (see that file's header for
   the pip installs — CPU is fine).
4. The script writes `public/models/golfpose_yolox_s.onnx`. Commit it.

Without this file the app silently falls back to motion-diff club tracking.
