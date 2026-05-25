"use client";

import { useEffect, useRef, useState } from "react";
import {
  FilesetResolver,
  PoseLandmarker,
  type NormalizedLandmark,
} from "@mediapipe/tasks-vision";
import {
  type CameraAngle,
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
import {
  type SwingGrade,
  combineGrades,
  gradeSwing,
} from "./lib/grade";

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

// Analysis pass tuning.
// Play the video slower so MediaPipe gets more wall-clock time per frame
// (fewer dropped frames on the fast part of the swing).
const ANALYSIS_PLAYBACK_RATE = 0.5;
// Trim this many seconds off the END of the clip — the walk-away / re-tee
// footage after the swing pollutes phase detection.
const ANALYSIS_TRIM_END_S = 5;
// Never trim so much that less than this remains to analyze.
const ANALYSIS_MIN_KEEP_S = 2;

export default function Home() {
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileSize, setFileSize] = useState<number | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [progress, setProgress] = useState(0);
  const [frameCount, setFrameCount] = useState(0);
  const [orientation, setOrientation] = useState<
    "auto" | "portrait" | "landscape"
  >("auto");
  const [videoAspect, setVideoAspect] = useState<number | null>(null);
  const [cameraAngle, setCameraAngle] = useState<CameraAngle>("face_on");
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [cacheNote, setCacheNote] = useState<string | null>(null);
  const [savedSwings, setSavedSwings] = useState<SwingRecord[]>([]);
  const [measurements, setMeasurements] = useState<Measurements | null>(null);
  const [combinedGrade, setCombinedGrade] = useState<SwingGrade | null>(null);
  const [combineNote, setCombineNote] = useState<string | null>(null);

  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const landmarkerRef = useRef<PoseLandmarker | null>(null);
  const framesRef = useRef<Frame[]>([]);
  const playbackRafRef = useRef<number | null>(null);
  const lastDetectTsRef = useRef<number>(0);

  const recordPreviewRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const [cameraOn, setCameraOn] = useState(false);
  const [recording, setRecording] = useState(false);
  const [recordError, setRecordError] = useState<string | null>(null);

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

  function computeNow(): Measurements | null {
    const frames = framesRef.current;
    if (frames.length === 0) return null;
    const v = videoRef.current;
    const w = v?.videoWidth || 1920;
    const h = v?.videoHeight || 1080;
    try {
      return computeMeasurements(frames, w, h);
    } catch (err) {
      console.warn("Measurement computation failed", err);
      return null;
    }
  }

  function recomputeMeasurements() {
    setMeasurements(computeNow());
  }

  async function refreshSavedSwings() {
    try {
      setSavedSwings(await listSwings());
    } catch (e) {
      console.warn("Could not list saved swings", e);
    }
  }

  async function ingestVideo(name: string, size: number, url: string) {
    if (videoUrl) URL.revokeObjectURL(videoUrl);
    setVideoUrl(url);
    setFileName(name);
    setFileSize(size);
    framesRef.current = [];
    setFrameCount(0);
    setProgress(0);
    setCacheNote(null);
    setMeasurements(null);
    clearCanvas();
    if (status !== "loading-model" && status !== "error") setStatus("ready");

    try {
      const cached = await loadSwing(makeKey(name, size));
      if (cached) {
        framesRef.current = cached.frames;
        setFrameCount(cached.frames.length);
        if (cached.cameraAngle) setCameraAngle(cached.cameraAngle);
        setCacheNote(
          `Loaded ${cached.frames.length} cached frames from ${new Date(
            cached.savedAt,
          ).toLocaleString()}`,
        );
        setStatus("done");
        if (cached.measurements) setMeasurements(cached.measurements);
        // Wait until next tick so the new <video> can begin loading metadata
        // before measurement computation reads videoWidth/Height.
        else setTimeout(() => recomputeMeasurements(), 0);
      }
    } catch (err) {
      console.warn("Cache lookup failed", err);
    }
  }

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    await ingestVideo(file.name, file.size, URL.createObjectURL(file));
  }

  function pickRecordMime(): string {
    const candidates = [
      "video/mp4",
      "video/webm;codecs=vp9",
      "video/webm;codecs=vp8",
      "video/webm",
    ];
    for (const c of candidates) {
      if (
        typeof MediaRecorder !== "undefined" &&
        MediaRecorder.isTypeSupported(c)
      )
        return c;
    }
    return "";
  }

  async function openCamera() {
    setRecordError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment", width: 1280, height: 720 },
        audio: false,
      });
      streamRef.current = stream;
      setCameraOn(true);
      // Attach after state flips so the preview element exists.
      setTimeout(() => {
        if (recordPreviewRef.current) {
          recordPreviewRef.current.srcObject = stream;
          void recordPreviewRef.current.play().catch(() => {});
        }
      }, 0);
    } catch (err) {
      setRecordError(
        err instanceof Error
          ? `Camera access failed: ${err.message}`
          : "Camera access failed.",
      );
    }
  }

  function closeCamera() {
    if (recorderRef.current && recording) {
      try {
        recorderRef.current.stop();
      } catch {
        // ignore
      }
    }
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (recordPreviewRef.current) recordPreviewRef.current.srcObject = null;
    setCameraOn(false);
    setRecording(false);
  }

  function startRecording() {
    const stream = streamRef.current;
    if (!stream) return;
    const mimeType = pickRecordMime();
    chunksRef.current = [];
    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(
        stream,
        mimeType ? { mimeType } : undefined,
      );
    } catch (err) {
      setRecordError(
        err instanceof Error ? err.message : "Could not start recording.",
      );
      return;
    }
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };
    recorder.onstop = async () => {
      const type = mimeType || "video/webm";
      const blob = new Blob(chunksRef.current, { type });
      const ext = type.includes("mp4") ? "mp4" : "webm";
      const name = `recording-${new Date()
        .toISOString()
        .replace(/[:.]/g, "-")}.${ext}`;
      // Release the camera once we have the clip.
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
      if (recordPreviewRef.current) recordPreviewRef.current.srcObject = null;
      setCameraOn(false);
      setRecording(false);
      await ingestVideo(name, blob.size, URL.createObjectURL(blob));
    };
    recorderRef.current = recorder;
    recorder.start();
    setRecording(true);
  }

  function stopRecording() {
    if (recorderRef.current && recording) recorderRef.current.stop();
  }

  useEffect(() => {
    return () => {
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  function syncCanvas() {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    if (video.videoWidth && video.videoHeight) {
      setVideoAspect(video.videoWidth / video.videoHeight);
    }
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

  function toggleSelected(key: string) {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function gradeSelected() {
    const selected = savedSwings.filter((s) => selectedKeys.has(s.key));
    const grades: SwingGrade[] = [];
    const skipped: string[] = [];
    for (const rec of selected) {
      if (!rec.measurements) {
        skipped.push(`${rec.fileName} (no stored analysis — re-run it)`);
        continue;
      }
      const g = gradeSwing(rec.measurements, rec.cameraAngle ?? "face_on");
      if (g.valid) grades.push(g);
      else skipped.push(`${rec.fileName} (no full swing detected)`);
    }
    if (grades.length === 0) {
      setCombinedGrade(null);
      setCombineNote(
        skipped.length
          ? `Couldn't grade: ${skipped.join("; ")}`
          : "Select swings to grade.",
      );
      return;
    }
    setCombinedGrade(combineGrades(grades));
    setCombineNote(
      `Combined ${grades.length} swing(s)${
        skipped.length ? `; skipped ${skipped.length}` : ""
      }.`,
    );
  }

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
    video.playbackRate = ANALYSIS_PLAYBACK_RATE;

    // How far into the clip we actually analyze (drop the trailing tail).
    const duration = video.duration || 0;
    const analysisEndS =
      duration > ANALYSIS_TRIM_END_S + ANALYSIS_MIN_KEEP_S
        ? duration - ANALYSIS_TRIM_END_S
        : duration;
    const trimmedS = Math.max(0, duration - analysisEndS);

    try {
      await video.play();
    } catch (e) {
      video.playbackRate = 1;
      setError(e instanceof Error ? e.message : String(e));
      setStatus("error");
      return;
    }

    let finished = false;
    const finish = async () => {
      if (finished) return;
      finished = true;
      video.removeEventListener("ended", onEnded);
      video.pause();
      video.playbackRate = 1;
      setStatus("saving");
      const computed = computeNow();
      try {
        const durationMs = (videoRef.current?.duration ?? 0) * 1000;
        await saveSwing({
          key: makeKey(fileName, fileSize),
          fileName,
          fileSize,
          durationMs,
          frames: framesRef.current,
          savedAt: Date.now(),
          cameraAngle,
          videoWidth: videoRef.current?.videoWidth,
          videoHeight: videoRef.current?.videoHeight,
          measurements: computed,
        });
        await refreshSavedSwings();
        setCacheNote(
          trimmedS > 0.1
            ? `Saved ${framesRef.current.length} frames (last ${trimmedS.toFixed(1)}s trimmed).`
            : `Saved ${framesRef.current.length} frames to cache.`,
        );
      } catch (err) {
        console.warn("Save failed", err);
        setCacheNote("Saved analysis in memory (cache save failed).");
      }
      setStatus("done");
      setProgress(1);
      setMeasurements(computed);
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
      const detectTs = Math.max(t_ms, lastDetectTsRef.current + 1);
      lastDetectTsRef.current = detectTs;
      const result = landmarkerRef.current.detectForVideo(v, detectTs);
      const landmarks = result.landmarks[0];
      if (landmarks) {
        framesRef.current.push({ t_ms, landmarks });
        setFrameCount(framesRef.current.length);
        drawSkeleton(landmarks);
      }
      if (analysisEndS > 0)
        setProgress(Math.min(1, metadata.mediaTime / analysisEndS));

      // Stop once we reach the trim point — don't analyze the trailing tail.
      if (metadata.mediaTime >= analysisEndS) {
        void finish();
        return;
      }
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
    if (record.cameraAngle) setCameraAngle(record.cameraAngle);
    clearCanvas();
    if (record.measurements) setMeasurements(record.measurements);
    else recomputeMeasurements();
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

  const isPortrait =
    orientation === "portrait" ||
    (orientation === "auto" && videoAspect !== null && videoAspect < 1);

  const statusText =
    status === "loading-model"
      ? "Loading pose model… (~10 MB, first time only)"
      : status === "ready"
        ? "Pose model loaded"
        : status === "analyzing"
          ? `Analyzing… ${Math.round(progress * 100)}% · ${frameCount} frames`
          : status === "saving"
            ? "Saving to cache…"
            : status === "done"
              ? `Ready · ${frameCount} frames`
              : status === "error"
                ? "Error"
                : "Idle";
  const statusDot =
    status === "error"
      ? "bg-red-500"
      : status === "analyzing" || status === "saving" || status === "loading-model"
        ? "bg-amber-500 animate-pulse"
        : status === "done" || status === "ready"
          ? "bg-emerald-500"
          : "bg-zinc-400";

  return (
    <main className="min-h-screen bg-zinc-50 dark:bg-zinc-950 text-zinc-900 dark:text-zinc-100">
      <div className="mx-auto max-w-3xl px-5 py-10 sm:px-6 sm:py-12">
        <header className="mb-8 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-emerald-500/15 ring-1 ring-emerald-500/30">
            <span className="h-3.5 w-3.5 rounded-full bg-emerald-500" />
          </div>
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight">Swing Coach</h1>
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              Upload a swing, run pose analysis, get a score.
            </p>
          </div>
          <div className="ml-auto inline-flex items-center gap-2 rounded-full border border-zinc-200 bg-white px-3 py-1.5 text-xs font-medium text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300">
            <span className={`h-2 w-2 rounded-full ${statusDot}`} />
            {statusText}
          </div>
        </header>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="group block cursor-pointer rounded-2xl border-2 border-dashed border-zinc-300 bg-white p-8 text-center transition-colors hover:border-emerald-400 hover:bg-emerald-50/40 dark:border-zinc-700 dark:bg-zinc-900 dark:hover:border-emerald-600 dark:hover:bg-emerald-950/20">
            <input
              type="file"
              accept="video/mp4,video/*"
              onChange={handleFile}
              className="hidden"
            />
            <div className="text-sm font-medium text-zinc-700 dark:text-zinc-200">
              {fileName ? "Choose a different video" : "Upload a swing video"}
            </div>
            <div className="mt-1 text-xs text-zinc-400">
              MP4 or any video file · stays on your device
            </div>
          </label>
          <button
            type="button"
            onClick={cameraOn ? closeCamera : openCamera}
            className="rounded-2xl border-2 border-dashed border-zinc-300 bg-white p-8 text-center transition-colors hover:border-emerald-400 hover:bg-emerald-50/40 dark:border-zinc-700 dark:bg-zinc-900 dark:hover:border-emerald-600 dark:hover:bg-emerald-950/20"
          >
            <div className="text-sm font-medium text-zinc-700 dark:text-zinc-200">
              {cameraOn ? "Close camera" : "Record a swing"}
            </div>
            <div className="mt-1 text-xs text-zinc-400">
              Use your device camera
            </div>
          </button>
        </div>

        {recordError && (
          <div className="mt-3 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            {recordError}
          </div>
        )}

        {cameraOn && (
          <div className="mt-4">
            <div className="relative overflow-hidden rounded-xl bg-black shadow-lg">
              <video
                ref={recordPreviewRef}
                muted
                playsInline
                autoPlay
                className="block w-full"
              />
              {recording && (
                <div className="absolute left-3 top-3 inline-flex items-center gap-1.5 rounded-full bg-black/60 px-2.5 py-1 text-xs font-medium text-white">
                  <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
                  REC
                </div>
              )}
            </div>
            <div className="mt-3 flex items-center gap-2">
              {!recording ? (
                <button
                  type="button"
                  onClick={startRecording}
                  className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-700"
                >
                  Start recording
                </button>
              ) : (
                <button
                  type="button"
                  onClick={stopRecording}
                  className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900"
                >
                  Stop &amp; use clip
                </button>
              )}
              <button
                type="button"
                onClick={closeCamera}
                className="rounded-lg border border-zinc-300 px-4 py-2 text-sm text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {error && (
          <div className="mt-3 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            {error}
          </div>
        )}
        {cacheNote && (
          <div className="mt-3 text-sm text-emerald-700 dark:text-emerald-400">
            {cacheNote}
          </div>
        )}

        {videoUrl && (
          <section className="mt-6">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-zinc-700 dark:text-zinc-300">
                {fileName}
              </span>
              <div className="flex items-center gap-2">
                <div className="inline-flex rounded-lg border border-zinc-200 p-0.5 dark:border-zinc-700">
                  {(["auto", "portrait", "landscape"] as const).map((o) => (
                    <button
                      key={o}
                      type="button"
                      onClick={() => setOrientation(o)}
                      className={`rounded-md px-2.5 py-1 text-xs font-medium capitalize transition-colors ${
                        orientation === o
                          ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
                          : "text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-100"
                      }`}
                    >
                      {o}
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  disabled={!canRun}
                  onClick={runAnalysis}
                  className="shrink-0 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {status === "analyzing"
                    ? "Analyzing…"
                    : framesRef.current.length > 0
                      ? "Re-run analysis"
                      : "Run analysis"}
                </button>
              </div>
            </div>

            <div className="mb-3 flex items-center gap-2 text-xs">
              <span className="text-zinc-500 dark:text-zinc-400">
                Camera angle:
              </span>
              <div className="inline-flex rounded-lg border border-zinc-200 p-0.5 dark:border-zinc-700">
                {(
                  [
                    ["face_on", "Face-on"],
                    ["down_the_line", "Down-the-line"],
                  ] as const
                ).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setCameraAngle(value)}
                    className={`rounded-md px-2.5 py-1 font-medium transition-colors ${
                      cameraAngle === value
                        ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
                        : "text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-100"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <span className="text-zinc-400">
                set this before running analysis
              </span>
            </div>

            <div
              className={`relative overflow-hidden rounded-xl bg-black shadow-lg ${
                isPortrait ? "mx-auto w-fit" : "w-full"
              }`}
            >
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
                className={
                  isPortrait
                    ? "mx-auto block max-h-[72vh] w-auto"
                    : "block w-full"
                }
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

            {measurements && (
              <PhaseTimeline
                measurements={measurements}
                onJump={(t_ms) => {
                  const v = videoRef.current;
                  if (!v) return;
                  v.currentTime = t_ms / 1000;
                  const frame = findNearestFrame(t_ms);
                  if (frame) drawSkeleton(frame.landmarks);
                }}
              />
            )}
          </section>
        )}

        {measurements && (
          <section className="mt-8 space-y-6">
            <GradeCard
              grade={gradeSwing(measurements, cameraAngle)}
              title={`Swing grade · ${cameraAngle === "down_the_line" ? "Down-the-line" : "Face-on"}`}
            />
            <MeasurementsPanel measurements={measurements} />
          </section>
        )}

        {savedSwings.length > 0 && (
          <section className="mt-10">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Saved swings
              </h2>
              <div className="flex items-center gap-2">
                <span className="text-xs text-zinc-400">
                  {selectedKeys.size} selected
                </span>
                <button
                  type="button"
                  disabled={selectedKeys.size === 0}
                  onClick={gradeSelected}
                  className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Grade selected together
                </button>
              </div>
            </div>
            <p className="mb-3 text-xs text-zinc-400">
              Select two or more angles of the same golfer (e.g. one face-on +
              one down-the-line). Each angle scores the factors it can measure,
              and they combine into one fuller scorecard.
            </p>
            <ul className="divide-y divide-zinc-200 rounded-lg border border-zinc-200 bg-white dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-900">
              {savedSwings.map((s) => {
                const angle = s.cameraAngle ?? "face_on";
                const angleLabel =
                  angle === "down_the_line" ? "DTL" : "Face-on";
                const selected = selectedKeys.has(s.key);
                return (
                  <li
                    key={s.key}
                    className="flex items-center justify-between gap-3 p-3 text-sm"
                  >
                    <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-3">
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={() => toggleSelected(s.key)}
                        className="h-4 w-4 shrink-0 accent-emerald-600"
                      />
                      <span className="min-w-0">
                        <span className="flex items-center gap-2">
                          <span className="truncate font-medium">
                            {s.fileName}
                          </span>
                          <span
                            className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                              angle === "down_the_line"
                                ? "bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300"
                                : "bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300"
                            }`}
                          >
                            {angleLabel}
                          </span>
                        </span>
                        <span className="block text-xs text-zinc-500 dark:text-zinc-400">
                          {s.frames.length} frames · saved{" "}
                          {new Date(s.savedAt).toLocaleString()}
                        </span>
                      </span>
                    </label>
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
                );
              })}
            </ul>
          </section>
        )}

        {(combinedGrade || combineNote) && (
          <section className="mt-8">
            {combineNote && (
              <div className="mb-3 text-xs text-zinc-500 dark:text-zinc-400">
                {combineNote}
              </div>
            )}
            {combinedGrade && combinedGrade.valid && (
              <GradeCard grade={combinedGrade} title="Combined scorecard" />
            )}
          </section>
        )}
      </div>
    </main>
  );
}

function scoreColor(score: number): string {
  if (score >= 8) return "text-emerald-600 dark:text-emerald-400";
  if (score >= 6) return "text-lime-600 dark:text-lime-400";
  if (score >= 4) return "text-amber-600 dark:text-amber-400";
  return "text-red-600 dark:text-red-400";
}

function scoreBarColor(score: number): string {
  if (score >= 8) return "bg-emerald-500";
  if (score >= 6) return "bg-lime-500";
  if (score >= 4) return "bg-amber-500";
  return "bg-red-500";
}

function GradeCard({ grade, title }: { grade: SwingGrade; title: string }) {
  if (!grade.valid) return null;
  return (
    <div className="rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="mb-4 flex items-center justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            {title}
          </h3>
          <div className="mt-1 text-sm font-medium text-zinc-700 dark:text-zinc-300">
            {grade.label}
          </div>
        </div>
        <div className="text-right">
          <div className={`text-4xl font-bold ${scoreColor(grade.overall)}`}>
            {grade.overall.toFixed(1)}
          </div>
          <div className="text-xs text-zinc-400">/ 10 overall</div>
        </div>
      </div>
      <div className="space-y-3">
        {grade.factors.map((f) => (
          <div key={f.key}>
            <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
              <span className="text-zinc-700 dark:text-zinc-300">
                {f.label}
              </span>
              <span className="flex items-baseline gap-2">
                <span className="font-mono text-xs text-zinc-400">
                  {f.value} · ideal {f.ideal}
                </span>
                <span className={`font-mono font-semibold ${scoreColor(f.score)}`}>
                  {f.score.toFixed(1)}
                </span>
              </span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
              <div
                className={`h-full ${scoreBarColor(f.score)}`}
                style={{ width: `${(f.score / 10) * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>
      {grade.focus && (
        <div className="mt-4 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300">
          Focus next: <span className="font-semibold">{grade.focus}</span> — the
          factor with the most room to gain.
        </div>
      )}
      <p className="mt-3 text-[11px] leading-snug text-zinc-400">
        Each factor is scored against published biomechanics bands. Depth-based
        factors (posture, early extension) only score from a down-the-line clip;
        turn and sway score from face-on.
      </p>
    </div>
  );
}

function PhaseTimeline({
  measurements,
  onJump,
}: {
  measurements: Measurements;
  onJump: (t_ms: number) => void;
}) {
  const phases = measurements.phases;
  const times = [phases.P1?.t_ms, phases.P4?.t_ms, phases.P7?.t_ms].filter(
    (t): t is number => typeof t === "number" && Number.isFinite(t),
  );
  if (times.length === 0) return null;
  const minT = Math.min(...times, phases.P1?.t_ms ?? 0);
  const maxT = Math.max(...times, phases.P7?.t_ms ?? Math.max(...times));
  const span = Math.max(maxT - minT, 1);

  const markers: { label: string; t_ms: number; color: string }[] = [];
  if (phases.P1) markers.push({ label: "P1", t_ms: phases.P1.t_ms, color: "bg-sky-500" });
  if (phases.P4) markers.push({ label: "P4", t_ms: phases.P4.t_ms, color: "bg-amber-500" });
  if (phases.P7) markers.push({ label: "P7", t_ms: phases.P7.t_ms, color: "bg-rose-500" });

  return (
    <div className="mt-4">
      <div className="mb-1 flex items-center justify-between text-xs text-zinc-500 dark:text-zinc-400">
        <span>Phases</span>
        <span className="font-mono">
          {(minT / 1000).toFixed(2)}s — {(maxT / 1000).toFixed(2)}s
        </span>
      </div>
      <div className="relative h-8 w-full rounded-md bg-zinc-200 dark:bg-zinc-800">
        {markers.map((m) => {
          const pct = ((m.t_ms - minT) / span) * 100;
          return (
            <button
              key={m.label}
              type="button"
              onClick={() => onJump(m.t_ms)}
              title={`${m.label} @ ${(m.t_ms / 1000).toFixed(2)}s`}
              className="absolute top-0 -translate-x-1/2 cursor-pointer select-none"
              style={{ left: `${pct}%` }}
            >
              <div className={`h-8 w-0.5 ${m.color}`} />
              <span className="mt-0.5 inline-block rounded bg-white px-1 text-[10px] font-medium text-zinc-700 shadow dark:bg-zinc-950 dark:text-zinc-200">
                {m.label}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function MeasurementsPanel({ measurements }: { measurements: Measurements }) {
  const { metrics, shoulderWidthPx, handedness } = measurements;
  const ks = metrics.kinematicSequence;
  const lh = metrics.leadHipDisplacement;

  const kinematicOrderStr =
    ks.order.length === 4
      ? `${ks.order.join(" → ")} ${ks.correct ? "✓" : "✗"}`
      : "insufficient data";

  const rows: { label: string; value: string }[] = [
    { label: "Handedness", value: handedness },
    {
      label: "Shoulder width (s_w)",
      value: `${shoulderWidthPx.toFixed(1)} px`,
    },
    { label: "Kinematic order", value: kinematicOrderStr },
    {
      label: "Spine angle change (P1→P7)",
      value: `${metrics.spineAngleChangeDeg.toFixed(1)}°`,
    },
    {
      label: "Head sway (P1→P4)",
      value: `${metrics.headSwayNorm.toFixed(2)} shoulders`,
    },
    {
      label: "Lead hip Δx (P4→P7)",
      value: `${lh.dx_norm.toFixed(2)} shoulders`,
    },
    {
      label: "Lead hip Δz (P4→P7)",
      value: `${lh.dz_norm.toFixed(2)} shoulders${lh.earlyExtension ? " (early extension)" : ""}`,
    },
    {
      label: "Pelvis rotation @ P4",
      value: `${metrics.rotation.pelvisRotationDegP4.toFixed(0)}°`,
    },
    {
      label: "Shoulder rotation @ P4",
      value: `${metrics.rotation.shoulderRotationDegP4.toFixed(0)}°`,
    },
    {
      label: "X-factor proxy @ P4",
      value: `${metrics.rotation.xFactorProxyDeg.toFixed(0)}°`,
    },
    {
      label: "Clubhead speed (proxy)",
      value: `${metrics.club.peakSpeedMph.toFixed(0)} mph (±15-20%)`,
    },
    {
      label: "Shaft-load proxy",
      value: `${metrics.club.shaftLoadProxy.toFixed(1)} N·(proxy)`,
    },
  ];

  return (
    <details
      open
      className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
    >
      <summary className="cursor-pointer text-sm font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        Measurements
      </summary>
      <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
        {rows.map((r) => (
          <div
            key={r.label}
            className="flex items-baseline justify-between gap-3 border-b border-zinc-100 pb-1 dark:border-zinc-800"
          >
            <dt className="text-zinc-600 dark:text-zinc-400">{r.label}</dt>
            <dd className="font-mono text-zinc-900 dark:text-zinc-100">
              {r.value}
            </dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
