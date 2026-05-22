"use client";

import { useEffect, useRef, useState } from "react";
import {
  FilesetResolver,
  PoseLandmarker,
  type NormalizedLandmark,
} from "@mediapipe/tasks-vision";

type Frame = {
  t_ms: number;
  landmarks: NormalizedLandmark[];
};

type Status =
  | "idle"
  | "loading-model"
  | "ready"
  | "analyzing"
  | "done"
  | "error";

const WASM_URL =
  "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.35/wasm";
const MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task";

export default function Home() {
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [progress, setProgress] = useState(0);
  const [frameCount, setFrameCount] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const landmarkerRef = useRef<PoseLandmarker | null>(null);
  const framesRef = useRef<Frame[]>([]);

  useEffect(() => {
    let cancelled = false;
    setStatus("loading-model");
    (async () => {
      try {
        const fileset = await FilesetResolver.forVisionTasks(WASM_URL);
        const landmarker = await PoseLandmarker.createFromOptions(fileset, {
          baseOptions: {
            modelAssetPath: MODEL_URL,
            delegate: "GPU",
          },
          runningMode: "VIDEO",
          numPoses: 1,
        });
        if (cancelled) {
          landmarker.close();
          return;
        }
        landmarkerRef.current = landmarker;
        setStatus("ready");
      } catch (e) {
        console.error(e);
        setError(e instanceof Error ? e.message : String(e));
        setStatus("error");
      }
    })();
    return () => {
      cancelled = true;
      landmarkerRef.current?.close();
      landmarkerRef.current = null;
    };
  }, []);

  useEffect(() => {
    return () => {
      if (videoUrl) URL.revokeObjectURL(videoUrl);
    };
  }, [videoUrl]);

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    if (videoUrl) URL.revokeObjectURL(videoUrl);
    setVideoUrl(URL.createObjectURL(file));
    setFileName(file.name);
    framesRef.current = [];
    setFrameCount(0);
    setProgress(0);
    if (status !== "loading-model" && status !== "error") setStatus("ready");
  }

  function syncCanvas() {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
  }

  function drawSkeleton(landmarks: NormalizedLandmark[]) {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    ctx.strokeStyle = "#22c55e";
    ctx.lineWidth = Math.max(2, W / 300);
    for (const conn of PoseLandmarker.POSE_CONNECTIONS) {
      const a = landmarks[conn.start];
      const b = landmarks[conn.end];
      if (!a || !b) continue;
      if ((a.visibility ?? 0) < 0.3 || (b.visibility ?? 0) < 0.3) continue;
      ctx.beginPath();
      ctx.moveTo(a.x * W, a.y * H);
      ctx.lineTo(b.x * W, b.y * H);
      ctx.stroke();
    }

    ctx.fillStyle = "#f97316";
    const r = Math.max(3, W / 200);
    for (const lm of landmarks) {
      if ((lm.visibility ?? 0) < 0.3) continue;
      ctx.beginPath();
      ctx.arc(lm.x * W, lm.y * H, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  async function runAnalysis() {
    const video = videoRef.current;
    const landmarker = landmarkerRef.current;
    if (!video || !landmarker) return;

    framesRef.current = [];
    setFrameCount(0);
    setProgress(0);
    setStatus("analyzing");
    setError(null);

    syncCanvas();
    video.pause();
    video.currentTime = 0;
    video.muted = true;
    try {
      await video.play();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus("error");
      return;
    }

    const tick = (
      _now: DOMHighResTimeStamp,
      metadata: VideoFrameCallbackMetadata,
    ) => {
      if (!landmarkerRef.current || !videoRef.current) return;
      const v = videoRef.current;
      const t_ms = metadata.mediaTime * 1000;
      const result = landmarkerRef.current.detectForVideo(v, t_ms);
      const landmarks = result.landmarks[0];
      if (landmarks) {
        framesRef.current.push({ t_ms, landmarks });
        setFrameCount(framesRef.current.length);
        drawSkeleton(landmarks);
      }
      if (v.duration) setProgress(metadata.mediaTime / v.duration);

      if (v.ended) {
        setStatus("done");
        setProgress(1);
        return;
      }
      v.requestVideoFrameCallback(tick);
    };
    video.requestVideoFrameCallback(tick);
  }

  const canRun =
    (status === "ready" || status === "done") && videoUrl !== null;

  return (
    <main className="min-h-screen bg-zinc-50 dark:bg-zinc-950 text-zinc-900 dark:text-zinc-100">
      <div className="mx-auto max-w-3xl px-6 py-12">
        <header className="mb-8">
          <h1 className="text-3xl font-semibold tracking-tight">Swing Coach</h1>
          <p className="mt-2 text-zinc-600 dark:text-zinc-400">
            Upload a swing video, then run pose analysis.
          </p>
        </header>

        <label className="block">
          <span className="sr-only">Choose a video file</span>
          <input
            type="file"
            accept="video/mp4,video/*"
            onChange={handleFile}
            className="block w-full cursor-pointer rounded-lg border border-dashed border-zinc-300 bg-white p-6 text-sm text-zinc-600 file:mr-4 file:rounded-md file:border-0 file:bg-zinc-900 file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:file:bg-zinc-100 dark:file:text-zinc-900"
          />
        </label>

        <div className="mt-4 text-sm text-zinc-600 dark:text-zinc-400">
          {status === "loading-model" &&
            "Loading pose model… (~10 MB, first time only)"}
          {status === "ready" && "Pose model loaded."}
          {status === "analyzing" &&
            `Analyzing… ${Math.round(progress * 100)}% — ${frameCount} frames captured`}
          {status === "done" && `Done. ${frameCount} frames captured.`}
          {status === "error" && (
            <span className="text-red-600">Error: {error}</span>
          )}
        </div>

        {videoUrl && (
          <section className="mt-6">
            <div className="mb-3 flex items-center justify-between gap-3">
              <span className="truncate text-sm text-zinc-500 dark:text-zinc-400">
                {fileName}
              </span>
              <button
                type="button"
                disabled={!canRun}
                onClick={runAnalysis}
                className="shrink-0 rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
              >
                {status === "analyzing" ? "Analyzing…" : "Run analysis"}
              </button>
            </div>

            <div className="relative w-full overflow-hidden rounded-lg bg-black shadow">
              <video
                ref={videoRef}
                src={videoUrl}
                controls
                playsInline
                onLoadedMetadata={syncCanvas}
                className="block w-full"
              />
              <canvas
                ref={canvasRef}
                className="pointer-events-none absolute inset-0 h-full w-full"
              />
            </div>

            {status === "analyzing" && (
              <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
                <div
                  className="h-full bg-emerald-500 transition-[width] duration-100"
                  style={{ width: `${Math.round(progress * 100)}%` }}
                />
              </div>
            )}
          </section>
        )}
      </div>
    </main>
  );
}
