#!/usr/bin/env python3
"""Export GolfPose's YOLOX-s (person+club) detector to ONNX for the browser.

Self-contained: reimplements mmdet's YOLOX-s in plain PyTorch with the exact
state_dict key naming, so the checkpoint loads with NO mmdet/mmcv install.

Input : models/golfpose_detector_2cls_yolox_s.pth
        (public download: http://gofile.me/4RvCV/ALgsmvtPw — from the
        GolfPose repo, https://github.com/MingHanLee/GolfPose)
Output: public/models/golfpose_yolox_s.onnx
        Decode is done in-graph. Output [1, 8400, 7]:
        (cx, cy, w, h, obj, p_person, p_club) in 640x640 letterbox pixels.

Setup:  pip install torch onnx        (CPU is fine)
Run:    python scripts/export_golfpose_onnx.py            # real weights
        python scripts/export_golfpose_onnx.py --dry-run  # random weights,
                                                          # pipeline check only

Architecture (configs/mmdet/golfpose_detector_2cls_yolox_s.py):
  CSPDarknet(deepen 0.33, widen 0.5) + YOLOXPAFPN(in [128,256,512], out 128,
  1 CSP block) + YOLOXHead(2 classes, 2 stacked convs), strides (8, 16, 32).
"""

import argparse
import sys
from pathlib import Path

import torch
import torch.nn as nn

CKPT = Path("models/golfpose_detector_2cls_yolox_s.pth")
OUT = Path("public/models/golfpose_yolox_s.onnx")
SIZE = 640
STRIDES = (8, 16, 32)
NUM_CLASSES = 2

BN = dict(eps=0.001, momentum=0.03)


class ConvModule(nn.Module):
    """conv + bn + SiLU, with mmdet's .conv / .bn key names."""

    def __init__(self, c_in, c_out, k, s=1):
        super().__init__()
        self.conv = nn.Conv2d(c_in, c_out, k, s, k // 2, bias=False)
        self.bn = nn.BatchNorm2d(c_out, **BN)
        self.activate = nn.SiLU(inplace=True)

    def forward(self, x):
        return self.activate(self.bn(self.conv(x)))


class DarknetBottleneck(nn.Module):
    def __init__(self, c, add_identity):
        super().__init__()
        self.conv1 = ConvModule(c, c, 1)
        self.conv2 = ConvModule(c, c, 3)
        self.add_identity = add_identity

    def forward(self, x):
        out = self.conv2(self.conv1(x))
        return x + out if self.add_identity else out


class CSPLayer(nn.Module):
    def __init__(self, c_in, c_out, n_blocks, add_identity=True):
        super().__init__()
        mid = c_out // 2
        self.main_conv = ConvModule(c_in, mid, 1)
        self.short_conv = ConvModule(c_in, mid, 1)
        self.final_conv = ConvModule(2 * mid, c_out, 1)
        self.blocks = nn.Sequential(
            *[DarknetBottleneck(mid, add_identity) for _ in range(n_blocks)]
        )

    def forward(self, x):
        a = self.blocks(self.main_conv(x))
        b = self.short_conv(x)
        return self.final_conv(torch.cat((a, b), dim=1))


class Focus(nn.Module):
    """Space-to-depth stem; mmdet slice order: TL, BL, TR, BR."""

    def __init__(self, c_in, c_out, k):
        super().__init__()
        self.conv = ConvModule(c_in * 4, c_out, k)

    def forward(self, x):
        tl = x[..., ::2, ::2]
        bl = x[..., 1::2, ::2]
        tr = x[..., ::2, 1::2]
        br = x[..., 1::2, 1::2]
        return self.conv(torch.cat((tl, bl, tr, br), dim=1))


class SPPBottleneck(nn.Module):
    def __init__(self, c_in, c_out, kernels=(5, 9, 13)):
        super().__init__()
        mid = c_in // 2
        self.conv1 = ConvModule(c_in, mid, 1)
        self.poolings = nn.ModuleList(
            [nn.MaxPool2d(k, stride=1, padding=k // 2) for k in kernels]
        )
        self.conv2 = ConvModule(mid * (len(kernels) + 1), c_out, 1)

    def forward(self, x):
        x = self.conv1(x)
        return self.conv2(torch.cat([x] + [p(x) for p in self.poolings], dim=1))


class CSPDarknet(nn.Module):
    """YOLOX-s scale: deepen 0.33 -> blocks (1,3,3,1); widen 0.5."""

    def __init__(self):
        super().__init__()
        self.stem = Focus(3, 32, 3)
        self.stage1 = nn.Sequential(ConvModule(32, 64, 3, 2), CSPLayer(64, 64, 1))
        self.stage2 = nn.Sequential(ConvModule(64, 128, 3, 2), CSPLayer(128, 128, 3))
        self.stage3 = nn.Sequential(ConvModule(128, 256, 3, 2), CSPLayer(256, 256, 3))
        self.stage4 = nn.Sequential(
            ConvModule(256, 512, 3, 2),
            SPPBottleneck(512, 512),
            CSPLayer(512, 512, 1, add_identity=False),
        )

    def forward(self, x):
        x = self.stem(x)
        x = self.stage1(x)
        c3 = self.stage2(x)
        c4 = self.stage3(c3)
        c5 = self.stage4(c4)
        return c3, c4, c5  # 128, 256, 512 channels


class YOLOXPAFPN(nn.Module):
    def __init__(self, in_ch=(128, 256, 512), out_ch=128):
        super().__init__()
        self.upsample = nn.Upsample(scale_factor=2, mode="nearest")
        self.reduce_layers = nn.ModuleList(
            [ConvModule(in_ch[2], in_ch[1], 1), ConvModule(in_ch[1], in_ch[0], 1)]
        )
        self.top_down_blocks = nn.ModuleList(
            [
                CSPLayer(in_ch[1] * 2, in_ch[1], 1, add_identity=False),
                CSPLayer(in_ch[0] * 2, in_ch[0], 1, add_identity=False),
            ]
        )
        self.downsamples = nn.ModuleList(
            [ConvModule(in_ch[0], in_ch[0], 3, 2), ConvModule(in_ch[1], in_ch[1], 3, 2)]
        )
        self.bottom_up_blocks = nn.ModuleList(
            [
                CSPLayer(in_ch[0] * 2, in_ch[1], 1, add_identity=False),
                CSPLayer(in_ch[1] * 2, in_ch[2], 1, add_identity=False),
            ]
        )
        self.out_convs = nn.ModuleList([ConvModule(c, out_ch, 1) for c in in_ch])

    def forward(self, feats):
        c3, c4, c5 = feats
        # top-down
        p5 = self.reduce_layers[0](c5)
        p4 = self.top_down_blocks[0](torch.cat((self.upsample(p5), c4), dim=1))
        p4r = self.reduce_layers[1](p4)
        p3 = self.top_down_blocks[1](torch.cat((self.upsample(p4r), c3), dim=1))
        # bottom-up
        n3 = p3
        n4 = self.bottom_up_blocks[0](torch.cat((self.downsamples[0](n3), p4r), dim=1))
        n5 = self.bottom_up_blocks[1](torch.cat((self.downsamples[1](n4), p5), dim=1))
        return (
            self.out_convs[0](n3),
            self.out_convs[1](n4),
            self.out_convs[2](n5),
        )


class YOLOXHead(nn.Module):
    def __init__(self, num_classes=NUM_CLASSES, feat=128, stacked=2, levels=3):
        super().__init__()
        def branch():
            return nn.Sequential(*[ConvModule(feat, feat, 3) for _ in range(stacked)])

        self.multi_level_cls_convs = nn.ModuleList([branch() for _ in range(levels)])
        self.multi_level_reg_convs = nn.ModuleList([branch() for _ in range(levels)])
        self.multi_level_conv_cls = nn.ModuleList(
            [nn.Conv2d(feat, num_classes, 1) for _ in range(levels)]
        )
        self.multi_level_conv_reg = nn.ModuleList(
            [nn.Conv2d(feat, 4, 1) for _ in range(levels)]
        )
        self.multi_level_conv_obj = nn.ModuleList(
            [nn.Conv2d(feat, 1, 1) for _ in range(levels)]
        )

    def forward(self, feats):
        out = []
        for i, f in enumerate(feats):
            cls_feat = self.multi_level_cls_convs[i](f)
            reg_feat = self.multi_level_reg_convs[i](f)
            out.append(
                (
                    self.multi_level_conv_cls[i](cls_feat),
                    self.multi_level_conv_reg[i](reg_feat),
                    self.multi_level_conv_obj[i](reg_feat),
                )
            )
        return out


class GolfPoseYOLOX(nn.Module):
    """backbone+neck+head with in-graph decode -> [B, N, 7]."""

    def __init__(self):
        super().__init__()
        self.backbone = CSPDarknet()
        self.neck = YOLOXPAFPN()
        self.bbox_head = YOLOXHead()
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
        levels = self.bbox_head(self.neck(self.backbone(x)))
        flat = []
        for cls, box, obj in levels:
            b = cls.shape[0]
            flat.append(
                torch.cat(
                    (
                        box.permute(0, 2, 3, 1).reshape(b, -1, 4),
                        obj.permute(0, 2, 3, 1).reshape(b, -1, 1),
                        cls.permute(0, 2, 3, 1).reshape(b, -1, NUM_CLASSES),
                    ),
                    dim=-1,
                )
            )
        p = torch.cat(flat, dim=1)
        xy = (p[..., 0:2] + self.grid) * self.stride
        wh = torch.exp(p[..., 2:4]) * self.stride
        scores = torch.sigmoid(p[..., 4:])
        return torch.cat((xy, wh, scores), dim=-1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="export random weights")
    args = ap.parse_args()

    model = GolfPoseYOLOX().eval()

    if not args.dry_run:
        if not CKPT.exists():
            sys.exit(
                f"checkpoint not found: {CKPT}\n"
                "Download (public, no auth): http://gofile.me/4RvCV/ALgsmvtPw"
            )
        ckpt = torch.load(CKPT, map_location="cpu", weights_only=False)
        state = ckpt.get("state_dict", ckpt)
        # mmdet checkpoints may carry an EMA copy and data_preprocessor stats.
        state = {
            k: v
            for k, v in state.items()
            if not k.startswith(("ema_", "data_preprocessor."))
        }
        missing, unexpected = model.load_state_dict(state, strict=False)
        # grid/stride are our own decode buffers; BN counters are irrelevant.
        missing = [
            m
            for m in missing
            if "num_batches_tracked" not in m and m not in ("grid", "stride")
        ]
        print(f"load: missing={len(missing)} unexpected={len(unexpected)}")
        if missing:
            print("MISSING:", missing[:20])
        if unexpected:
            print("UNEXPECTED:", unexpected[:20])
        if missing:
            sys.exit("state_dict mismatch — inspect keys above and fix the model")

    dummy = torch.randn(1, 3, SIZE, SIZE)
    with torch.no_grad():
        out = model(dummy)
    print("decoded output:", tuple(out.shape))
    assert tuple(out.shape) == (1, 8400, 4 + 1 + NUM_CLASSES)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model,
        dummy,
        str(OUT),
        input_names=["images"],
        output_names=["preds"],
        opset_version=12,
        do_constant_folding=True,
        dynamo=False,
    )
    print(f"exported {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
