"use client";

import { useEffect, useRef, useState } from "react";

export default function Home() {
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

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
  }

  return (
    <main className="min-h-screen bg-zinc-50 dark:bg-zinc-950 text-zinc-900 dark:text-zinc-100">
      <div className="mx-auto max-w-3xl px-6 py-12">
        <header className="mb-8">
          <h1 className="text-3xl font-semibold tracking-tight">Swing Coach</h1>
          <p className="mt-2 text-zinc-600 dark:text-zinc-400">
            Upload a swing video to get started.
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

        {videoUrl && (
          <section className="mt-8">
            <div className="mb-3 text-sm text-zinc-500 dark:text-zinc-400">
              {fileName}
            </div>
            <video
              ref={videoRef}
              src={videoUrl}
              controls
              playsInline
              className="w-full rounded-lg bg-black shadow"
            />
          </section>
        )}
      </div>
    </main>
  );
}
