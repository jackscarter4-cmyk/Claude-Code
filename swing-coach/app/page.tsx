"use client";

import { useEffect, useRef, useState } from "react";
import {
  FilesetResolver,
  PoseLandmarker,
  type NormalizedLandmark,
} from "@mediapipe/tasks-vision";
import {
  type Frame,
  type SwingRecord,
  deleteSwing,
  listSwings,
  loadSwing,
  makeKey,
  saveSwing,
} from "./lib/db";
import {
  computeMeasurements,
  type Measurements,
} from "./lib/metrics";

type Status =
  | "idle"
  | "loading-model"
  | "ready"
  | "analyzing"
  | "saving"
  | "done"
  | "error";

const WASM_URL =
  "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.35/wasm";
const MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task";

export default function Home() {
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileSize, setFileSize] = useState<number | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [progress, setProgress] = useState(0);
  const [frameCount, setFrameCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [cacheNote, setCacheNote] = useState<string | null>(null);
  const [savedSwings, setSavedSwings] = useState<SwingRecord[]>([]);
  const [measurements, setMeasurements] = useState<Measurements | null>(null);
  const [showDiagnosePayload, setShowDiagnosePayload] = useState(false);

  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const landmarkerRef = useRef<PoseLandmarker | null>(null);
  const framesRef = useRef<Frame[]>([]);
  const playbackRafRef = useRef<number | null>(null);

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
    refreshSavedSwings();
  }, []);

  useEffect(() => {
    return () => {
      if (videoUrl) URL.revokeObjectURL(videoUrl);
    };
  }, [videoUrl]);

  function recomputeMeasurements() {
    const frames = framesRef.current;
    if (frames.length === 0) {
      setMeasurements(null);
      return;
    }
    const v = videoRef.current;
    const w = v?.videoWidth || 1920;
    const h = v?.videoHeight || 1080;
    try {
      setMeasurements(computeMeasurements(frames, w, h));
    } catch (err) {
      console.warn("Measurement computation failed", err);
      setMeasurements(null);
    }
  }

  async function refreshSavedSwings() {
    try {
      setSavedSwings(await listSwings());
    } catch (e) {
      console.warn("Could not list saved swings", e);
    }
  }

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    if (videoUrl) URL.revokeObjectURL(videoUrl);
    const url = URL.createObjectURL(file);
    setVideoUrl(url);
    setFileName(file.name);
    setFileSize(file.size);
    framesRef.current = [];
    setFrameCount(0);
    setProgress(0);
    setCacheNote(null);
    setMeasurements(null);
    setShowDiagnosePayload(false);
    clearCanvas();
    if (status !== "loading-model" && status !== "error") setStatus("ready");

    try {
      const cached = await loadSwing(makeKey(file.name, file.size));
      if (cached) {
        framesRef.current = cached.frames;
        setFrameCount(cached.frames.length);
        setCacheNote(
          `Loaded ${cached.frames.length} cached frames from ${new Date(
            cached.savedAt,
          ).toLocaleString()}`,
        );
        setStatus("done");
        // Wait until next tick so the new <video> can begin loading metadata
        // before measurement computation reads videoWidth/Height.
        setTimeout(() => recomputeMeasurements(), 0);
      }
    } catch (err) {
      console.warn("Cache lookup failed", err);
    }
  }

  function syncCanvas() {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
  }

  function clearCanvas() {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    ctx?.clearRect(0, 0, canvas.width, canvas.height);
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

  function findNearestFrame(t_ms: number): Frame | null {
    const frames = framesRef.current;
    if (frames.length === 0) return null;
    let lo = 0;
    let hi = frames.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (frames[mid].t_ms < t_ms) lo = mid + 1;
      else hi = mid;
    }
    const cand = frames[lo];
    const prev = lo > 0 ? frames[lo - 1] : cand;
    return Math.abs(cand.t_ms - t_ms) < Math.abs(prev.t_ms - t_ms)
      ? cand
      : prev;
  }

  function startPlaybackOverlay() {
    const video = videoRef.current;
    if (!video) return;
    if (playbackRafRef.current != null) {
      cancelAnimationFrame(playbackRafRef.current);
    }
    const loop = () => {
      const v = videoRef.current;
      if (!v) return;
      const frame = findNearestFrame(v.currentTime * 1000);
      if (frame) drawSkeleton(frame.landmarks);
      playbackRafRef.current = requestAnimationFrame(loop);
    };
    playbackRafRef.current = requestAnimationFrame(loop);
  }

  function stopPlaybackOverlay() {
    if (playbackRafRef.current != null) {
      cancelAnimationFrame(playbackRafRef.current);
      playbackRafRef.current = null;
    }
  }

  useEffect(() => {
    return () => stopPlaybackOverlay();
  }, []);

  async function runAnalysis() {
    const video = videoRef.current;
    const landmarker = landmarkerRef.current;
    if (!video || !landmarker || !fileName || fileSize == null) return;

    stopPlaybackOverlay();
    framesRef.current = [];
    setFrameCount(0);
    setProgress(0);
    setCacheNote(null);
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

    let finished = false;
    const finish = async () => {
      if (finished) return;
      finished = true;
      video.removeEventListener("ended", onEnded);
      setStatus("saving");
      try {
        const durationMs = (videoRef.current?.duration ?? 0) * 1000;
        await saveSwing({
          key: makeKey(fileName, fileSize),
          fileName,
          fileSize,
          durationMs,
          frames: framesRef.current,
          savedAt: Date.now(),
        });
        await refreshSavedSwings();
        setCacheNote(`Saved ${framesRef.current.length} frames to cache.`);
      } catch (err) {
        console.warn("Save failed", err);
        setCacheNote("Saved analysis in memory (cache save failed).");
      }
      setStatus("done");
      setProgress(1);
    };
    const onEnded = () => {
      void finish();
    };
    video.addEventListener("ended", onEnded);

    const tick = (
      _now: DOMHighResTimeStamp,
      metadata: VideoFrameCallbackMetadata,
    ) => {
      if (finished) return;
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
        void finish();
        return;
      }
      v.requestVideoFrameCallback(tick);
    };
    video.requestVideoFrameCallback(tick);
  }

  async function reopenSwing(record: SwingRecord) {
    setVideoUrl(null);
    setFileName(record.fileName);
    setFileSize(record.fileSize);
    framesRef.current = record.frames;
    setFrameCount(record.frames.length);
    setProgress(1);
    setCacheNote(
      `Reopened cached analysis (${record.frames.length} frames). Re-select the video file to view it.`,
    );
    setStatus("done");
    clearCanvas();
  }

  async function removeSwing(key: string) {
    await deleteSwing(key);
    await refreshSavedSwings();
  }

  const canRun =
    (status === "ready" ||
      status === "done" ||
      (status === "idle" && landmarkerRef.current != null)) &&
    videoUrl !== null;

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

        <div className="mt-4 space-y-1 text-sm text-zinc-600 dark:text-zinc-400">
          {status === "loading-model" &&
            "Loading pose model… (~10 MB, first time only)"}
          {status === "ready" && "Pose model loaded."}
          {status === "analyzing" &&
            `Analyzing… ${Math.round(progress * 100)}% — ${frameCount} frames captured`}
          {status === "saving" && "Saving to cache…"}
          {status === "done" && `Done. ${frameCount} frames captured.`}
          {status === "error" && (
            <span className="text-red-600">Error: {error}</span>
          )}
          {cacheNote && (
            <div className="text-emerald-700 dark:text-emerald-400">
              {cacheNote}
            </div>
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
                {status === "analyzing"
                  ? "Analyzing…"
                  : framesRef.current.length > 0
                    ? "Re-run analysis"
                    : "Run analysis"}
              </button>
            </div>

            <div className="relative w-full overflow-hidden rounded-lg bg-black shadow">
              <video
                ref={videoRef}
                src={videoUrl}
                controls
                playsInline
                onLoadedMetadata={syncCanvas}
                onPlay={startPlaybackOverlay}
                onPause={stopPlaybackOverlay}
                onSeeked={() => {
                  const v = videoRef.current;
                  if (!v) return;
                  const frame = findNearestFrame(v.currentTime * 1000);
                  if (frame) drawSkeleton(frame.landmarks);
                }}
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

        {savedSwings.length > 0 && (
          <section className="mt-10">
            <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Saved swings
            </h2>
            <ul className="divide-y divide-zinc-200 rounded-lg border border-zinc-200 bg-white dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-900">
              {savedSwings.map((s) => (
                <li
                  key={s.key}
                  className="flex items-center justify-between gap-3 p-3 text-sm"
                >
                  <div className="min-w-0">
                    <div className="truncate font-medium">{s.fileName}</div>
                    <div className="text-xs text-zinc-500 dark:text-zinc-400">
                      {s.frames.length} frames · saved{" "}
                      {new Date(s.savedAt).toLocaleString()}
                    </div>
                  </div>
                  <div className="flex shrink-0 gap-2">
                    <button
                      type="button"
                      onClick={() => reopenSwing(s)}
                      className="rounded-md border border-zinc-300 px-2 py-1 text-xs hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
                    >
                      Reopen
                    </button>
                    <button
                      type="button"
                      onClick={() => removeSwing(s.key)}
                      className="rounded-md border border-zinc-300 px-2 py-1 text-xs text-red-600 hover:bg-red-50 dark:border-zinc-700 dark:hover:bg-red-950"
                    >
                      Delete
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </main>
  );
}
