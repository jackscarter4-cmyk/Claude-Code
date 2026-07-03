// In-browser club detection using the GolfPose YOLOX-s (person+club) model,
// exported to ONNX by scripts/export_golfpose_onnx.py and served from
// /models/golfpose_yolox_s.onnx. When the model file isn't deployed, callers
// get null and the app falls back to motion-diff tracking.
//
// The exported graph decodes in-graph: output [1, 8400, 7] rows of
// (cx, cy, w, h, obj, p_person, p_club) in 640x640 letterbox pixel space.

import * as ort from "onnxruntime-web";

const MODEL_URL = "/models/golfpose_yolox_s.onnx";
const SIZE = 640;
const PAD = 114;
const SCORE_THRESH = 0.3;
const NMS_IOU = 0.45;
const CLUB_CLS = 6; // column index of p_club
const PERSON_CLS = 5;

export type ClubDetection = {
  // Normalized [0..1] video coords.
  box: { x1: number; y1: number; x2: number; y2: number };
  score: number;
};

let sessionPromise: Promise<ort.InferenceSession | null> | null = null;
let scratch: HTMLCanvasElement | null = null;

/** Loads the model once; resolves null if it isn't deployed. */
export function getClubSession(): Promise<ort.InferenceSession | null> {
  if (!sessionPromise) {
    sessionPromise = (async () => {
      try {
        const head = await fetch(MODEL_URL, { method: "HEAD" });
        if (!head.ok) return null;
        return await ort.InferenceSession.create(MODEL_URL, {
          executionProviders: ["webgpu", "wasm"],
        });
      } catch (err) {
        console.warn("Club model unavailable:", err);
        return null;
      }
    })();
  }
  return sessionPromise;
}

type Letterbox = { scale: number; padX: number; padY: number };

function letterboxToTensor(video: HTMLVideoElement): {
  tensor: ort.Tensor;
  lb: Letterbox;
} | null {
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  if (!vw || !vh) return null;
  if (!scratch) scratch = document.createElement("canvas");
  scratch.width = SIZE;
  scratch.height = SIZE;
  const ctx = scratch.getContext("2d", { willReadFrequently: true });
  if (!ctx) return null;

  const scale = Math.min(SIZE / vw, SIZE / vh);
  const dw = Math.round(vw * scale);
  const dh = Math.round(vh * scale);
  const padX = Math.floor((SIZE - dw) / 2);
  const padY = Math.floor((SIZE - dh) / 2);
  ctx.fillStyle = `rgb(${PAD},${PAD},${PAD})`;
  ctx.fillRect(0, 0, SIZE, SIZE);
  ctx.drawImage(video, padX, padY, dw, dh);

  const rgba = ctx.getImageData(0, 0, SIZE, SIZE).data;
  const n = SIZE * SIZE;
  const data = new Float32Array(3 * n);
  // mmdet loads images as BGR and this config applies no normalization,
  // so feed raw 0-255 values in B,G,R channel order.
  for (let i = 0; i < n; i++) {
    data[i] = rgba[i * 4 + 2]; // B
    data[n + i] = rgba[i * 4 + 1]; // G
    data[2 * n + i] = rgba[i * 4]; // R
  }
  return {
    tensor: new ort.Tensor("float32", data, [1, 3, SIZE, SIZE]),
    lb: { scale, padX, padY },
  };
}

function iou(a: number[], b: number[]): number {
  const x1 = Math.max(a[0], b[0]);
  const y1 = Math.max(a[1], b[1]);
  const x2 = Math.min(a[2], b[2]);
  const y2 = Math.min(a[3], b[3]);
  const inter = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
  const areaA = (a[2] - a[0]) * (a[3] - a[1]);
  const areaB = (b[2] - b[0]) * (b[3] - b[1]);
  return inter / (areaA + areaB - inter + 1e-9);
}

/** Detect the club in the video's current frame. Null if none/unavailable. */
export async function detectClub(
  session: ort.InferenceSession,
  video: HTMLVideoElement,
): Promise<ClubDetection | null> {
  const prep = letterboxToTensor(video);
  if (!prep) return null;
  const { tensor, lb } = prep;
  const outputs = await session.run({ images: tensor });
  const preds = outputs.preds ?? outputs[session.outputNames[0]];
  const data = preds.data as Float32Array;
  const rows = preds.dims[1];

  // Collect club candidates (score = obj * p_club, and club must outscore person).
  const cands: { box: number[]; score: number }[] = [];
  for (let i = 0; i < rows; i++) {
    const o = i * 7;
    const obj = data[o + 4];
    const pClub = data[o + CLUB_CLS];
    if (data[o + PERSON_CLS] > pClub) continue;
    const score = obj * pClub;
    if (score < SCORE_THRESH) continue;
    const cx = data[o];
    const cy = data[o + 1];
    const w = data[o + 2];
    const h = data[o + 3];
    cands.push({
      box: [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2],
      score,
    });
  }
  if (cands.length === 0) return null;

  cands.sort((a, b) => b.score - a.score);
  const kept: typeof cands = [];
  for (const c of cands) {
    if (kept.every((k) => iou(k.box, c.box) < NMS_IOU)) kept.push(c);
    if (kept.length >= 3) break;
  }
  const best = kept[0];

  // Undo letterbox back to normalized video coords.
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  const toNorm = (x: number, y: number) => ({
    x: Math.min(1, Math.max(0, (x - lb.padX) / lb.scale / vw)),
    y: Math.min(1, Math.max(0, (y - lb.padY) / lb.scale / vh)),
  });
  const p1 = toNorm(best.box[0], best.box[1]);
  const p2 = toNorm(best.box[2], best.box[3]);
  return {
    box: { x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y },
    score: best.score,
  };
}

/**
 * The clubhead is the end of the club farthest from the hands: pick the box
 * corner with max distance from the wrist (normalized coords).
 */
export function clubheadFromBox(
  det: ClubDetection,
  wristX: number,
  wristY: number,
): { x: number; y: number } {
  const { x1, y1, x2, y2 } = det.box;
  const corners = [
    { x: x1, y: y1 },
    { x: x2, y: y1 },
    { x: x1, y: y2 },
    { x: x2, y: y2 },
  ];
  let best = corners[0];
  let bestD = -1;
  for (const c of corners) {
    const d = (c.x - wristX) ** 2 + (c.y - wristY) ** 2;
    if (d > bestD) {
      bestD = d;
      best = c;
    }
  }
  return best;
}
