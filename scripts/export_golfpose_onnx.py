#!/usr/bin/env python3
"""Export GolfPose's YOLOX-s (person+club) detector to ONNX for the browser.

Input : models/golfpose_detector_2cls_yolox_s.pth
        (public download: http://gofile.me/4RvCV/ALgsmvtPw — from the
        GolfPose repo README, https://github.com/MingHanLee/GolfPose)
Output: public/models/golfpose_yolox_s.onnx
        Raw head outputs are decoded IN-GRAPH, so the JS side only does
        thresholding + NMS. Output shape: [1, N, 7] where each row is
        (cx, cy, w, h, obj, p_person, p_club) in 640x640 letterbox pixels.

Setup (CPU is fine):
  pip install torch --index-url https://download.pytorch.org/whl/cpu
  pip install mmengine "mmcv>=2.0.0" mmdet onnx

Run from the repo root:
  python scripts/export_golfpose_onnx.py

Architecture (from GolfPose configs/mmdet/golfpose_detector_2cls_yolox_s.py):
  YOLOX-s = CSPDarknet(deepen 0.33, widen 0.5) + YOLOXPAFPN(out 128)
            + YOLOXHead(num_classes=2), strides (8, 16, 32), input 640x640.
"""

import sys
from pathlib import Path

import torch

CKPT = Path("models/golfpose_detector_2cls_yolox_s.pth")
OUT = Path("public/models/golfpose_yolox_s.onnx")
SIZE = 640
STRIDES = (8, 16, 32)


def build_model():
    """Build the mmdet YOLOX-s model matching the GolfPose config."""
    from mmdet.registry import MODELS
    from mmengine.registry import init_default_scope

    init_default_scope("mmdet")
    cfg = dict(
        type="YOLOX",
        data_preprocessor=dict(type="DetDataPreprocessor", pad_size_divisor=32),
        backbone=dict(
            type="CSPDarknet",
            deepen_factor=0.33,
            widen_factor=0.5,
            out_indices=(2, 3, 4),
            use_depthwise=False,
            spp_kernal_sizes=(5, 9, 13),
            norm_cfg=dict(type="BN", momentum=0.03, eps=0.001),
            act_cfg=dict(type="Swish"),
        ),
        neck=dict(
            type="YOLOXPAFPN",
            in_channels=[128, 256, 512],
            out_channels=128,
            num_csp_blocks=1,
            use_depthwise=False,
            upsample_cfg=dict(scale_factor=2, mode="nearest"),
            norm_cfg=dict(type="BN", momentum=0.03, eps=0.001),
            act_cfg=dict(type="Swish"),
        ),
        bbox_head=dict(
            type="YOLOXHead",
            num_classes=2,
            in_channels=128,
            feat_channels=128,
            stacked_convs=2,
            strides=STRIDES,
            use_depthwise=False,
            norm_cfg=dict(type="BN", momentum=0.03, eps=0.001),
            act_cfg=dict(type="Swish"),
        ),
    )
    model = MODELS.build(cfg)
    ckpt = torch.load(CKPT, map_location="cpu")
    state = ckpt.get("state_dict", ckpt)
    missing, unexpected = model.load_state_dict(state, strict=False)
    print(f"loaded state_dict: missing={len(missing)} unexpected={len(unexpected)}")
    if missing:
        print("  missing keys (first 10):", missing[:10])
    model.eval()
    return model


class DecodedYOLOX(torch.nn.Module):
    """Wraps backbone+neck+head and decodes predictions in-graph."""

    def __init__(self, det):
        super().__init__()
        self.backbone = det.backbone
        self.neck = det.neck
        self.head = det.bbox_head
        # Precompute grid centers + strides for all levels: [N, 2] and [N, 1]
        grids, strides = [], []
        for s in STRIDES:
            n = SIZE // s
            ys, xs = torch.meshgrid(
                torch.arange(n, dtype=torch.float32),
                torch.arange(n, dtype=torch.float32),
                indexing="ij",
            )
            grids.append(torch.stack((xs, ys), dim=-1).reshape(-1, 2))
            strides.append(torch.full((n * n, 1), float(s)))
        self.register_buffer("grid", torch.cat(grids, dim=0))
        self.register_buffer("stride", torch.cat(strides, dim=0))

    def forward(self, x):
        feats = self.neck(self.backbone(x))
        cls_scores, bbox_preds, objectnesses = self.head(feats)
        flat = []
        for cls, box, obj in zip(cls_scores, bbox_preds, objectnesses):
            b = cls.shape[0]
            # [B, C, H, W] -> [B, H*W, C]
            flat.append(
                torch.cat(
                    (
                        box.permute(0, 2, 3, 1).reshape(b, -1, 4),
                        obj.permute(0, 2, 3, 1).reshape(b, -1, 1),
                        cls.permute(0, 2, 3, 1).reshape(b, -1, 2),
                    ),
                    dim=-1,
                )
            )
        p = torch.cat(flat, dim=1)  # [B, N, 7]
        xy = (p[..., 0:2] + self.grid) * self.stride
        wh = torch.exp(p[..., 2:4]) * self.stride
        scores = torch.sigmoid(p[..., 4:7])
        return torch.cat((xy, wh, scores), dim=-1)  # [B, N, 7]


def main():
    if not CKPT.exists():
        sys.exit(
            f"checkpoint not found: {CKPT}\n"
            "Download it (public link, no auth) from "
            "http://gofile.me/4RvCV/ALgsmvtPw and place it there."
        )
    det = build_model()
    model = DecodedYOLOX(det).eval()
    dummy = torch.randn(1, 3, SIZE, SIZE)
    with torch.no_grad():
        out = model(dummy)
    print("decoded output shape:", tuple(out.shape))  # (1, 8400, 7)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model,
        dummy,
        str(OUT),
        input_names=["images"],
        output_names=["preds"],
        opset_version=12,
        do_constant_folding=True,
    )
    print(f"exported {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
