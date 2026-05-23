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
  extrapolateClubhead,
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
  const [playheadMs, setPlayheadMs] = useState(0);
  const [frameCount, setFrameCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [cacheNote, setCacheNote] = useState<string | null>(null);
  const [savedSwings, setSavedSwings] = useState<SwingRecord[]>([]);
  const [measurements, setMeasurements] = useState<Measurements | null>(null);
  const [showDiagnosePayload, setShowDiagnosePayload] = useState(false);
  const [diagnosis, setDiagnosis] = useState<string | null>(null);
  const [diagnosisLoading, setDiagnosisLoading] = useState(false);
  const [diagnosisError, setDiagnosisError] = useState<string | null>(null);
  const [diagnosisUsage, setDiagnosisUsage] = useState<{
    input_tokens?: number;
    output_tokens?: number;
    cache_creation_input_tokens?: number;
    cache_read_input_tokens?: number;
  } | null>(null);

  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const landmarkerRef = useRef<PoseLandmarker | null>(null);
  const framesRef = useRef<Frame[]>([]);
  const playbackRafRef = useRef<number | null>(null);
  const lastDetectTsRef = useRef<number>(0);

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
    setDiagnosis(null);
    setDiagnosisError(null);
    setDiagnosisUsage(null);
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

    // Extrapolated club (shaft + head), once we know the body scale.
    if (measurements && measurements.shoulderWidthPx > 0) {
      const leadSide = measurements.handedness === "right" ? "left" : "right";
      const wristIdx = leadSide === "left" ? 15 : 16;
      const wrist = landmarks[wristIdx];
      const head = extrapolateClubhead(
        landmarks,
        leadSide,
        measurements.shoulderWidthPx,
        W,
        H,
      );
      if (wrist && head && (wrist.visibility ?? 0) > 0.3) {
        ctx.strokeStyle = "#38bdf8";
        ctx.lineWidth = Math.max(2, W / 350);
        ctx.beginPath();
        ctx.moveTo(wrist.x * W, wrist.y * H);
        ctx.lineTo(head.x * W, head.y * H);
        ctx.stroke();
        ctx.fillStyle = "#0ea5e9";
        ctx.beginPath();
        ctx.arc(head.x * W, head.y * H, r * 1.4, 0, Math.PI * 2);
        ctx.fill();
      }
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
      const t = v.currentTime * 1000;
      const frame = findNearestFrame(t);
      if (frame) drawSkeleton(frame.landmarks);
      setPlayheadMs(t);
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

  function buildDiagnosePayload() {
    const frames = framesRef.current;
    const fps =
      frames.length > 1
        ? Math.round(
            (1000 * (frames.length - 1)) /
              (frames[frames.length - 1].t_ms - frames[0].t_ms),
          )
        : 0;
    return {
      camera_angle: "face_on",
      handedness: measurements?.handedness ?? "right",
      club: "iron",
      golfer_level: "intermediate",
      fps,
      fileName,
      fileSize,
      frameCount: frames.length,
      measurements,
    };
  }

  function sanityCheck(payload: ReturnType<typeof buildDiagnosePayload>) {
    const errors: string[] = [];
    const m = payload.measurements;
    if (!m) {
      errors.push("No measurements computed.");
      return errors;
    }
    const p = m.phases;

    if (!p.P1 || !p.P4 || !p.P7) {
      const missing = (["P1", "P4", "P7"] as const).filter((k) => !p[k]);
      errors.push(`Phase detection missing ${missing.join(", ")}.`);
      return errors;
    }

    if (payload.frameCount < 30)
      errors.push(
        `Only ${payload.frameCount} frames captured — need at least 30 to cover P1–P8.`,
      );

    if (p.P4.frame <= p.P1.frame)
      errors.push(
        `P4 (frame ${p.P4.frame}) is not after P1 (frame ${p.P1.frame}) — phase detection failed.`,
      );
    if (p.P7.frame <= p.P4.frame)
      errors.push(
        `P7 (frame ${p.P7.frame}) is not after P4 (frame ${p.P4.frame}) — phase detection failed.`,
      );

    if (p.P7.frame - p.P4.frame < 5)
      errors.push(
        `P4 and P7 are ${p.P7.frame - p.P4.frame} frames apart — phase detection failed.`,
      );

    if (p.P1.frame < 10)
      errors.push(
        `P1 at frame ${p.P1.frame} — too close to video start, likely false.`,
      );
    if (p.P7.frame > payload.frameCount - 10)
      errors.push(
        `P7 at frame ${p.P7.frame} of ${payload.frameCount} — too close to video end, likely false.`,
      );

    const ks = m.metrics.kinematicSequence;
    const peakTimes = new Set([
      ks.pelvisPeakMs,
      ks.thoraxPeakMs,
      ks.armPeakMs,
      ks.clubPeakMs,
    ]);
    if (peakTimes.size < 4)
      errors.push(
        `Kinematic peaks not distinct: ${peakTimes.size} unique times.`,
      );

    return errors;
  }

  async function runDiagnosis() {
    if (!measurements) return;
    const payload = buildDiagnosePayload();
    const errors = sanityCheck(payload);
    if (errors.length > 0) {
      setDiagnosis(null);
      setDiagnosisUsage(null);
      setDiagnosisError(
        `Pose data failed sanity checks — not sending to Claude:\n• ${errors.join(
          "\n• ",
        )}`,
      );
      return;
    }
    setDiagnosisLoading(true);
    setDiagnosisError(null);
    setDiagnosis(null);
    setDiagnosisUsage(null);
    try {
      const res = await fetch("/api/diagnose", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) {
        setDiagnosisError(data.error ?? `Request failed (${res.status})`);
      } else {
        setDiagnosis(data.text ?? "(no text returned)");
        setDiagnosisUsage(data.usage ?? null);
      }
    } catch (err) {
      setDiagnosisError(err instanceof Error ? err.message : String(err));
    } finally {
      setDiagnosisLoading(false);
    }
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
      recomputeMeasurements();
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
    setShowDiagnosePayload(false);
    clearCanvas();
    recomputeMeasurements();
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
                  const t = v.currentTime * 1000;
                  const frame = findNearestFrame(t);
                  if (frame) drawSkeleton(frame.landmarks);
                  setPlayheadMs(t);
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
          <section className="mt-8">
            <ForceTransferHeatmap
              measurements={measurements}
              currentTimeMs={playheadMs}
            />
            <div className="mt-6" />
            <MeasurementsPanel measurements={measurements} />
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={diagnosisLoading}
                onClick={runDiagnosis}
                className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {diagnosisLoading ? "Asking Claude…" : "Diagnose with Claude"}
              </button>
              <button
                type="button"
                onClick={() => setShowDiagnosePayload((v) => !v)}
                className="rounded-md border border-zinc-300 px-3 py-2 text-xs text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
              >
                {showDiagnosePayload ? "Hide" : "Show"} raw payload
              </button>
            </div>

            {diagnosisError && (
              <div className="mt-3 whitespace-pre-wrap rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
                {diagnosisError}
              </div>
            )}

            {diagnosis && (
              <div className="mt-4 rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                    Claude diagnosis
                  </h3>
                  {diagnosisUsage && (
                    <span className="text-xs text-zinc-500 dark:text-zinc-400">
                      {diagnosisUsage.input_tokens ?? 0} in ·{" "}
                      {diagnosisUsage.output_tokens ?? 0} out
                      {diagnosisUsage.cache_read_input_tokens != null &&
                        diagnosisUsage.cache_read_input_tokens > 0 &&
                        ` · ${diagnosisUsage.cache_read_input_tokens} cached`}
                    </span>
                  )}
                </div>
                <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-zinc-900 dark:text-zinc-100">
                  {diagnosis}
                </pre>
              </div>
            )}

            {showDiagnosePayload && (
              <pre className="mt-3 max-h-80 overflow-auto rounded-lg border border-zinc-200 bg-zinc-50 p-3 text-xs text-zinc-800 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-200">
                {JSON.stringify(buildDiagnosePayload(), null, 2)}
              </pre>
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

function heatColor(v: number): string {
  // 0 -> cool blue, 1 -> hot red. v in [0,1].
  const t = Math.min(1, Math.max(0, v));
  const hue = 220 - 220 * t; // 220 (blue) -> 0 (red)
  const light = 30 + 35 * t;
  return `hsl(${hue}, 85%, ${light}%)`;
}

function ForceTransferHeatmap({
  measurements,
  currentTimeMs,
}: {
  measurements: Measurements;
  currentTimeMs: number;
}) {
  const series = measurements.sequenceSeries;
  if (!series || series.t_ms.length === 0) return null;

  // Nearest sample to the current playhead.
  let idx = 0;
  let best = Infinity;
  for (let i = 0; i < series.t_ms.length; i++) {
    const d = Math.abs(series.t_ms[i] - currentTimeMs);
    if (d < best) {
      best = d;
      idx = i;
    }
  }

  const links: { name: string; v: number }[] = [
    { name: "Pelvis", v: series.pelvis[idx] ?? 0 },
    { name: "Thorax", v: series.thorax[idx] ?? 0 },
    { name: "Arm", v: series.arm[idx] ?? 0 },
    { name: "Club", v: series.club[idx] ?? 0 },
  ];

  return (
    <div className="mt-6 rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          Power transfer (speed through the chain)
        </h3>
        <span className="text-xs text-zinc-400">play the video to animate</span>
      </div>
      <div className="grid grid-cols-4 gap-2">
        {links.map((l) => (
          <div key={l.name} className="text-center">
            <div
              className="mx-auto h-16 w-full rounded-md transition-colors duration-75"
              style={{ backgroundColor: heatColor(l.v) }}
            />
            <div className="mt-1 text-xs font-medium text-zinc-700 dark:text-zinc-300">
              {l.name}
            </div>
            <div className="font-mono text-[10px] text-zinc-400">
              {Math.round(l.v * 100)}%
            </div>
          </div>
        ))}
      </div>
      <p className="mt-3 text-[11px] leading-snug text-zinc-400">
        Each block shows that segment&apos;s speed (0-100% of its own max) at the
        current frame. An efficient swing lights up left-to-right: pelvis →
        thorax → arm → club. This visualizes sequence/speed transfer, not
        measured force.
      </p>
    </div>
  );
}
