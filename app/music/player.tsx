"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  type Playlist,
  type Track,
  formatDuration,
  thumbUrl,
} from "./lib/types";
import { lookupTitle, parseVideoInput, searchYouTube } from "./lib/search";
import {
  loadApiKey,
  loadHistory,
  loadPlaylists,
  loadQueue,
  loadQueueIndex,
  pushHistory,
  saveApiKey,
  savePlaylists,
  saveQueue,
  saveQueueIndex,
} from "./lib/store";

// ---- Minimal typings for the YouTube IFrame Player API ----------------------

type YTPlayer = {
  loadVideoById: (videoId: string) => void;
  playVideo: () => void;
  pauseVideo: () => void;
  seekTo: (seconds: number, allowSeekAhead: boolean) => void;
  getCurrentTime: () => number;
  getDuration: () => number;
  getVideoData?: () => { title?: string; author?: string };
  destroy: () => void;
};

type YTNamespace = {
  Player: new (
    el: HTMLElement,
    opts: {
      width?: string;
      height?: string;
      playerVars?: Record<string, string | number>;
      events?: {
        onReady?: () => void;
        onStateChange?: (e: { data: number }) => void;
        onError?: (e: { data: number }) => void;
      };
    },
  ) => YTPlayer;
  PlayerState: {
    ENDED: number;
    PLAYING: number;
    PAUSED: number;
    BUFFERING: number;
    CUED: number;
  };
};

declare global {
  interface Window {
    YT?: YTNamespace;
    onYouTubeIframeAPIReady?: () => void;
  }
}

let ytApiPromise: Promise<YTNamespace> | null = null;

function loadYouTubeApi(): Promise<YTNamespace> {
  if (ytApiPromise) return ytApiPromise;
  ytApiPromise = new Promise((resolve) => {
    if (window.YT?.Player) {
      resolve(window.YT);
      return;
    }
    const prev = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      prev?.();
      if (window.YT) resolve(window.YT);
    };
    if (!document.querySelector('script[src*="youtube.com/iframe_api"]')) {
      const tag = document.createElement("script");
      tag.src = "https://www.youtube.com/iframe_api";
      document.head.appendChild(tag);
    }
  });
  return ytApiPromise;
}

// -----------------------------------------------------------------------------

type Tab = "search" | "queue" | "playlists";

const PLACEHOLDER_TITLE = "YouTube video";

export default function Player() {
  // Library state
  const [queue, setQueue] = useState<Track[]>([]);
  const [qIndex, setQIndex] = useState(0);
  const [playlists, setPlaylists] = useState<Playlist[]>([]);
  const [history, setHistory] = useState<Track[]>([]);

  // Playback state
  const [isPlaying, setIsPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [duration, setDuration] = useState(0);
  const [loopQueue, setLoopQueue] = useState(true);
  const [videoExpanded, setVideoExpanded] = useState(false);
  const [keepAwake, setKeepAwake] = useState(true);

  // Search state
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Track[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searchedOnce, setSearchedOnce] = useState(false);

  // UI state
  const [tab, setTab] = useState<Tab>("search");
  const [showSettings, setShowSettings] = useState(false);
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [saveName, setSaveName] = useState("");
  const [hydrated, setHydrated] = useState(false);

  const playerRef = useRef<YTPlayer | null>(null);
  const playerReadyRef = useRef(false);
  const playerHostRef = useRef<HTMLDivElement | null>(null);
  const anchorRef = useRef<HTMLAudioElement | null>(null);
  const queueRef = useRef<Track[]>([]);
  const qIndexRef = useRef(0);
  const loopRef = useRef(true);
  const pendingIdRef = useRef<string | null>(null);
  // Whether playback *should* be running: lets us tell a user pause apart
  // from the browser force-pausing the video when the app goes to the
  // background, so we can fight the latter and respect the former.
  const intendedPlayingRef = useRef(false);
  const resumeAttemptsRef = useRef(0);

  const current: Track | undefined = queue[qIndex];

  // ---- Hydrate persisted state ----
  // localStorage can only be read after hydration (the page is prerendered at
  // build time), so an effect is the right place despite the lint rule.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    setQueue(loadQueue());
    setQIndex(loadQueueIndex());
    setPlaylists(loadPlaylists());
    setHistory(loadHistory());
    setApiKey(loadApiKey());
    setHydrated(true);
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  useEffect(() => {
    if (hydrated) saveQueue(queue);
  }, [queue, hydrated]);
  useEffect(() => {
    if (hydrated) saveQueueIndex(qIndex);
  }, [qIndex, hydrated]);
  useEffect(() => {
    if (hydrated) savePlaylists(playlists);
  }, [playlists, hydrated]);

  // ---- Player plumbing ----

  const startPlayback = useCallback((track: Track) => {
    intendedPlayingRef.current = true;
    if (playerReadyRef.current && playerRef.current) {
      playerRef.current.loadVideoById(track.id);
    } else {
      pendingIdRef.current = track.id;
    }
    // The anchor <audio> is silent; keeping it playing lets this page (not the
    // YouTube iframe) own the lock-screen/notification media controls where
    // the browser allows it.
    anchorRef.current?.play().catch(() => {});
    setHistory(pushHistory(track));
  }, []);

  const playAt = useCallback(
    (index: number) => {
      const track = queueRef.current[index];
      if (!track) return;
      setQIndex(index);
      setProgress(0);
      startPlayback(track);
    },
    [startPlayback],
  );

  const playAtRef = useRef(playAt);

  // Keep refs in sync so the YouTube player's event callbacks (created once)
  // always see the latest queue state.
  useEffect(() => {
    queueRef.current = queue;
    qIndexRef.current = qIndex;
    loopRef.current = loopQueue;
    playAtRef.current = playAt;
  });

  const next = useCallback(() => {
    const q = queueRef.current;
    const i = qIndexRef.current;
    if (i + 1 < q.length) playAtRef.current(i + 1);
    else if (loopRef.current && q.length > 0) playAtRef.current(0);
    else intendedPlayingRef.current = false;
  }, []);

  const prev = useCallback(() => {
    const p = playerRef.current;
    if (p && p.getCurrentTime() > 5) {
      p.seekTo(0, true);
      return;
    }
    const i = qIndexRef.current;
    if (i > 0) playAtRef.current(i - 1);
    else p?.seekTo(0, true);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let player: YTPlayer | null = null;
    loadYouTubeApi().then((YT) => {
      if (cancelled || !playerHostRef.current) return;
      // The API replaces the host element, so give it a child to consume.
      const mount = document.createElement("div");
      playerHostRef.current.appendChild(mount);
      player = new YT.Player(mount, {
        width: "100%",
        height: "100%",
        playerVars: { playsinline: 1, rel: 0 },
        events: {
          onReady: () => {
            playerReadyRef.current = true;
            playerRef.current = player;
            if (pendingIdRef.current) {
              player?.loadVideoById(pendingIdRef.current);
              pendingIdRef.current = null;
            }
          },
          onStateChange: (e) => {
            if (e.data === YT.PlayerState.ENDED) {
              next();
            } else if (e.data === YT.PlayerState.PLAYING) {
              setIsPlaying(true);
              intendedPlayingRef.current = true;
              resumeAttemptsRef.current = 0;
              setDuration(player?.getDuration() ?? 0);
              // Fill in real titles for tracks added by pasted link.
              const q = queueRef.current;
              const i = qIndexRef.current;
              const data = player?.getVideoData?.();
              if (q[i] && q[i].title === PLACEHOLDER_TITLE && data?.title) {
                setQueue((cur) =>
                  cur.map((t, j) =>
                    j === i
                      ? {
                          ...t,
                          title: data.title ?? t.title,
                          channel: data.author ?? t.channel,
                        }
                      : t,
                  ),
                );
              }
            } else if (e.data === YT.PlayerState.PAUSED) {
              setIsPlaying(false);
              if (document.hidden && intendedPlayingRef.current) {
                // The browser paused us because the app went to the
                // background — the user didn't ask for this, so push back.
                // (A pause the user requests from the lock screen goes
                // through our media-session handler, which clears the
                // intended flag first.)
                if (resumeAttemptsRef.current < 10) {
                  resumeAttemptsRef.current += 1;
                  setTimeout(() => {
                    if (intendedPlayingRef.current && document.hidden)
                      player?.playVideo();
                  }, 300);
                }
              } else {
                // Paused while visible = a deliberate pause (our button or
                // the YouTube player's own controls).
                intendedPlayingRef.current = false;
              }
            }
          },
          onError: () => {
            // Unplayable (deleted, embed-disabled, region-locked): skip ahead.
            next();
          },
        },
      });
    });
    return () => {
      cancelled = true;
      playerReadyRef.current = false;
      playerRef.current = null;
      player?.destroy();
    };
  }, [next]);

  // While music plays, hold a screen wake lock so the phone doesn't auto-lock
  // (locking is what kills web playback most often). Released on pause and
  // re-acquired when the tab becomes visible again, per the API's lifecycle.
  useEffect(() => {
    if (!keepAwake || !isPlaying || !("wakeLock" in navigator)) return;
    let sentinel: WakeLockSentinel | null = null;
    let stopped = false;
    const acquire = async () => {
      try {
        const s = await navigator.wakeLock.request("screen");
        if (stopped) await s.release();
        else sentinel = s;
      } catch {
        // denied (e.g. battery saver) — nothing to do
      }
    };
    const onVisible = () => {
      if (!document.hidden) acquire();
    };
    acquire();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      document.removeEventListener("visibilitychange", onVisible);
      sentinel?.release().catch(() => {});
    };
  }, [keepAwake, isPlaying]);

  // If the browser managed to keep us paused in the background, pick the
  // song back up the moment the app is visible again (no-op if already
  // playing).
  useEffect(() => {
    const onVisible = () => {
      if (!document.hidden && intendedPlayingRef.current)
        playerRef.current?.playVideo();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);

  // Progress polling
  useEffect(() => {
    if (!isPlaying) return;
    const t = setInterval(() => {
      const p = playerRef.current;
      if (p) {
        setProgress(p.getCurrentTime());
        const d = p.getDuration();
        if (d > 0) setDuration(d);
      }
    }, 500);
    return () => clearInterval(t);
  }, [isPlaying]);

  // Lock-screen / media-key metadata and handlers
  useEffect(() => {
    if (!("mediaSession" in navigator)) return;
    if (current) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: current.title,
        artist: current.channel,
        album: "TubePlay",
        artwork: [{ src: current.thumb, sizes: "320x180", type: "image/jpeg" }],
      });
    }
    navigator.mediaSession.playbackState = isPlaying ? "playing" : "paused";
  }, [current, isPlaying]);

  useEffect(() => {
    if (!("mediaSession" in navigator)) return;
    const ms = navigator.mediaSession;
    ms.setActionHandler("play", () => {
      intendedPlayingRef.current = true;
      playerRef.current?.playVideo();
    });
    ms.setActionHandler("pause", () => {
      intendedPlayingRef.current = false;
      playerRef.current?.pauseVideo();
    });
    ms.setActionHandler("previoustrack", () => prev());
    ms.setActionHandler("nexttrack", () => next());
    try {
      ms.setActionHandler("seekto", (d) => {
        if (d.seekTime !== undefined)
          playerRef.current?.seekTo(d.seekTime, true);
      });
    } catch {
      // older browsers don't support seekto
    }
    return () => {
      ms.setActionHandler("play", null);
      ms.setActionHandler("pause", null);
      ms.setActionHandler("previoustrack", null);
      ms.setActionHandler("nexttrack", null);
    };
  }, [next, prev]);

  // ---- Actions ----

  const togglePlay = () => {
    const p = playerRef.current;
    if (!p) return;
    if (isPlaying) {
      intendedPlayingRef.current = false;
      p.pauseVideo();
      anchorRef.current?.pause();
    } else if (current) {
      intendedPlayingRef.current = true;
      p.playVideo();
      anchorRef.current?.play().catch(() => {});
    }
  };

  const playNow = (track: Track) => {
    setQueue((cur) => {
      const existing = cur.findIndex((t) => t.id === track.id);
      if (existing >= 0) {
        setQIndex(existing);
        return cur;
      }
      const insertAt = cur.length === 0 ? 0 : qIndexRef.current + 1;
      const nextQueue = [
        ...cur.slice(0, insertAt),
        track,
        ...cur.slice(insertAt),
      ];
      setQIndex(insertAt);
      return nextQueue;
    });
    setProgress(0);
    startPlayback(track);
  };

  const addToQueue = (track: Track) => {
    setQueue((cur) =>
      cur.some((t) => t.id === track.id) ? cur : [...cur, track],
    );
  };

  const removeAt = (i: number) => {
    setQueue((cur) => cur.filter((_, j) => j !== i));
    if (i < qIndex) setQIndex(qIndex - 1);
    else if (i === qIndex) setQIndex(Math.max(0, Math.min(qIndex, queue.length - 2)));
  };

  const moveTrack = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= queue.length) return;
    setQueue((cur) => {
      const copy = [...cur];
      [copy[i], copy[j]] = [copy[j], copy[i]];
      return copy;
    });
    if (qIndex === i) setQIndex(j);
    else if (qIndex === j) setQIndex(i);
  };

  const onSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;

    // A pasted link or bare video id plays immediately.
    const id = parseVideoInput(q);
    if (id) {
      const meta = await lookupTitle(id);
      playNow({
        id,
        title: meta?.title ?? PLACEHOLDER_TITLE,
        channel: meta?.channel ?? "",
        thumb: thumbUrl(id),
      });
      setQuery("");
      return;
    }

    setSearching(true);
    setSearchError(null);
    setSearchedOnce(true);
    try {
      setResults(await searchYouTube(q, apiKey));
    } catch (err) {
      setResults([]);
      setSearchError(err instanceof Error ? err.message : String(err));
    } finally {
      setSearching(false);
    }
  };

  const savePlaylist = () => {
    const name = saveName.trim();
    if (!name || queue.length === 0) return;
    setPlaylists((cur) => [
      { name, tracks: queue, updatedAt: Date.now() },
      ...cur.filter((p) => p.name !== name),
    ]);
    setSaveName("");
  };

  const loadPlaylist = (p: Playlist) => {
    setQueue(p.tracks);
    setQIndex(0);
    if (p.tracks[0]) startPlayback(p.tracks[0]);
    setTab("queue");
  };

  const seek = (value: number) => {
    setProgress(value);
    playerRef.current?.seekTo(value, true);
  };

  // ---- Render ----

  const trackRow = (track: Track, actions: React.ReactNode) => (
    <li
      key={track.id}
      className="flex items-center gap-3 rounded-lg p-2 hover:bg-white/5"
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={track.thumb}
        alt=""
        className="h-12 w-20 shrink-0 rounded object-cover bg-neutral-800"
        loading="lazy"
      />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{track.title}</p>
        <p className="truncate text-xs text-neutral-400">
          {track.channel}
          {track.durationSec ? ` · ${formatDuration(track.durationSec)}` : ""}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-1">{actions}</div>
    </li>
  );

  const iconBtn =
    "rounded-md px-2 py-1 text-xs font-medium bg-white/10 hover:bg-white/20 active:bg-white/25";

  return (
    <div className="flex min-h-dvh flex-col bg-neutral-950 text-neutral-100">
      {/* Header */}
      <header className="mx-auto flex w-full max-w-2xl items-center justify-between px-4 pt-5 pb-2">
        <div>
          <h1 className="text-xl font-bold tracking-tight">
            ▶ Tube<span className="text-red-500">Play</span>
          </h1>
          <p className="text-xs text-neutral-400">
            Search YouTube, queue it up, keep it playing.
          </p>
        </div>
        <button
          onClick={() => setShowSettings(true)}
          className={iconBtn}
          aria-label="Settings"
        >
          ⚙ Settings
        </button>
      </header>

      {/* Search */}
      <form
        onSubmit={onSearch}
        className="mx-auto flex w-full max-w-2xl gap-2 px-4 py-2"
      >
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search a song, or paste a YouTube link…"
          className="min-w-0 flex-1 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm outline-none placeholder:text-neutral-500 focus:border-red-500/60"
          inputMode="search"
        />
        <button
          type="submit"
          disabled={searching}
          className="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold hover:bg-red-500 disabled:opacity-50"
        >
          {searching ? "…" : "Search"}
        </button>
      </form>

      {/* Tabs */}
      <nav className="mx-auto flex w-full max-w-2xl gap-1 px-4 pb-1">
        {(
          [
            ["search", "Results"],
            ["queue", `Queue (${queue.length})`],
            ["playlists", "Playlists"],
          ] as [Tab, string][]
        ).map(([t, label]) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded-full px-3 py-1 text-xs font-medium ${
              tab === t
                ? "bg-white text-neutral-950"
                : "bg-white/10 text-neutral-300 hover:bg-white/15"
            }`}
          >
            {label}
          </button>
        ))}
      </nav>

      {/* Content */}
      <main className="mx-auto w-full max-w-2xl flex-1 px-4 pb-56 pt-2">
        {tab === "search" && (
          <>
            {searchError && (
              <p className="mb-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200">
                {searchError}
              </p>
            )}
            {results.length > 0 && (
              <ul className="space-y-1">
                {results.map((t) =>
                  trackRow(
                    t,
                    <>
                      <button onClick={() => playNow(t)} className={iconBtn}>
                        Play
                      </button>
                      <button
                        onClick={() => addToQueue(t)}
                        className={iconBtn}
                        aria-label="Add to queue"
                      >
                        +Queue
                      </button>
                    </>,
                  ),
                )}
              </ul>
            )}
            {results.length === 0 && !searching && (
              <div className="mt-2 space-y-4">
                {searchedOnce && !searchError && (
                  <p className="text-sm text-neutral-400">No results.</p>
                )}
                {history.length > 0 && (
                  <div>
                    <h2 className="mb-1 text-xs font-semibold uppercase tracking-wide text-neutral-500">
                      Recently played
                    </h2>
                    <ul className="space-y-1">
                      {history.slice(0, 15).map((t) =>
                        trackRow(
                          t,
                          <>
                            <button
                              onClick={() => playNow(t)}
                              className={iconBtn}
                            >
                              Play
                            </button>
                            <button
                              onClick={() => addToQueue(t)}
                              className={iconBtn}
                            >
                              +Queue
                            </button>
                          </>,
                        ),
                      )}
                    </ul>
                  </div>
                )}
                <details className="rounded-lg border border-white/10 bg-white/5 p-3 text-xs text-neutral-300">
                  <summary className="cursor-pointer font-semibold text-neutral-200">
                    Keep it playing with the screen off / other apps open
                  </summary>
                  <ul className="mt-2 list-disc space-y-1 pl-4">
                    <li>
                      <b>Desktop:</b> just switch tabs or apps — audio keeps
                      playing.
                    </li>
                    <li>
                      <b>Android:</b> use Brave or Firefox and enable
                      &ldquo;Background video playback&rdquo; in the browser
                      settings, then this page keeps playing with the screen
                      off. You can also &ldquo;Add to Home screen&rdquo; to use
                      it like an app.
                    </li>
                    <li>
                      <b>iPhone (Safari):</b> start a song, lock the phone,
                      then press play on the lock screen / Control Center —
                      audio resumes and keeps playing.
                    </li>
                    <li>
                      Playback uses the official YouTube player, so views still
                      count for the artists.
                    </li>
                  </ul>
                </details>
              </div>
            )}
          </>
        )}

        {tab === "queue" && (
          <>
            {queue.length === 0 ? (
              <p className="mt-4 text-sm text-neutral-400">
                Queue is empty — search for something and hit +Queue.
              </p>
            ) : (
              <>
                <div className="mb-2 flex items-center gap-2">
                  <input
                    value={saveName}
                    onChange={(e) => setSaveName(e.target.value)}
                    placeholder="Save queue as playlist…"
                    className="min-w-0 flex-1 rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs outline-none placeholder:text-neutral-500"
                  />
                  <button
                    onClick={savePlaylist}
                    disabled={!saveName.trim()}
                    className={`${iconBtn} disabled:opacity-40`}
                  >
                    Save
                  </button>
                  <button
                    onClick={() => {
                      setQueue([]);
                      setQIndex(0);
                    }}
                    className={iconBtn}
                  >
                    Clear
                  </button>
                </div>
                <ul className="space-y-1">
                  {queue.map((t, i) => (
                    <li
                      key={`${t.id}-${i}`}
                      className={`flex items-center gap-3 rounded-lg p-2 ${
                        i === qIndex ? "bg-red-500/15" : "hover:bg-white/5"
                      }`}
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={t.thumb}
                        alt=""
                        className="h-12 w-20 shrink-0 rounded object-cover bg-neutral-800"
                        loading="lazy"
                      />
                      <button
                        onClick={() => playAt(i)}
                        className="min-w-0 flex-1 text-left"
                      >
                        <p className="truncate text-sm font-medium">
                          {i === qIndex && (
                            <span className="mr-1 text-red-400">
                              {isPlaying ? "▶" : "❚❚"}
                            </span>
                          )}
                          {t.title}
                        </p>
                        <p className="truncate text-xs text-neutral-400">
                          {t.channel}
                          {t.durationSec
                            ? ` · ${formatDuration(t.durationSec)}`
                            : ""}
                        </p>
                      </button>
                      <div className="flex shrink-0 gap-1">
                        <button
                          onClick={() => moveTrack(i, -1)}
                          className={iconBtn}
                          aria-label="Move up"
                        >
                          ↑
                        </button>
                        <button
                          onClick={() => moveTrack(i, 1)}
                          className={iconBtn}
                          aria-label="Move down"
                        >
                          ↓
                        </button>
                        <button
                          onClick={() => removeAt(i)}
                          className={iconBtn}
                          aria-label="Remove"
                        >
                          ✕
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}

        {tab === "playlists" && (
          <>
            {playlists.length === 0 ? (
              <p className="mt-4 text-sm text-neutral-400">
                No playlists yet. Build a queue, then save it from the Queue
                tab.
              </p>
            ) : (
              <ul className="space-y-2">
                {playlists.map((p) => (
                  <li
                    key={p.name}
                    className="flex items-center gap-3 rounded-lg border border-white/10 bg-white/5 p-3"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold">{p.name}</p>
                      <p className="text-xs text-neutral-400">
                        {p.tracks.length} track{p.tracks.length === 1 ? "" : "s"}
                      </p>
                    </div>
                    <button onClick={() => loadPlaylist(p)} className={iconBtn}>
                      Play
                    </button>
                    <button
                      onClick={() =>
                        setQueue((cur) => [
                          ...cur,
                          ...p.tracks.filter(
                            (t) => !cur.some((c) => c.id === t.id),
                          ),
                        ])
                      }
                      className={iconBtn}
                    >
                      +Queue
                    </button>
                    <button
                      onClick={() =>
                        setPlaylists((cur) =>
                          cur.filter((x) => x.name !== p.name),
                        )
                      }
                      className={iconBtn}
                      aria-label={`Delete ${p.name}`}
                    >
                      ✕
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </main>

      {/* Player dock */}
      <div className="fixed inset-x-0 bottom-0 border-t border-white/10 bg-neutral-900/95 backdrop-blur">
        <div className="relative mx-auto w-full max-w-2xl px-4 py-3">
          {/* Video: always mounted and visible; toggles between mini and expanded */}
          <div
            className={`overflow-hidden rounded-lg bg-black shadow-lg ${
              videoExpanded
                ? "relative mb-3 aspect-video w-full"
                : "absolute bottom-full right-4 mb-2 aspect-video w-40"
            }`}
          >
            <div
              ref={playerHostRef}
              className="absolute inset-0 [&>*]:h-full [&>*]:w-full"
            />
          </div>

          {/* Seek bar */}
          <div className="flex items-center gap-2 text-[10px] text-neutral-400">
            <span className="w-9 text-right tabular-nums">
              {formatDuration(progress) || "0:00"}
            </span>
            <input
              type="range"
              min={0}
              max={Math.max(duration, 1)}
              step={1}
              value={Math.min(progress, duration || progress)}
              onChange={(e) => seek(Number(e.target.value))}
              className="h-1 flex-1 accent-red-500"
              aria-label="Seek"
            />
            <span className="w-9 tabular-nums">
              {formatDuration(duration) || "–:––"}
            </span>
          </div>

          <div className="mt-1 flex items-center gap-3">
            {current ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={current.thumb}
                alt=""
                className="h-10 w-[4.5rem] shrink-0 rounded object-cover bg-neutral-800"
              />
            ) : (
              <div className="h-10 w-[4.5rem] shrink-0 rounded bg-neutral-800" />
            )}
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">
                {current?.title ?? "Nothing playing"}
              </p>
              <p className="truncate text-xs text-neutral-400">
                {current?.channel ?? "Search a song to get started"}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <button
                onClick={prev}
                className="rounded-full p-2 text-lg hover:bg-white/10"
                aria-label="Previous"
              >
                ⏮
              </button>
              <button
                onClick={togglePlay}
                className="rounded-full bg-white px-4 py-2 text-lg text-neutral-950 hover:bg-neutral-200"
                aria-label={isPlaying ? "Pause" : "Play"}
              >
                {isPlaying ? "❚❚" : "▶"}
              </button>
              <button
                onClick={next}
                className="rounded-full p-2 text-lg hover:bg-white/10"
                aria-label="Next"
              >
                ⏭
              </button>
            </div>
          </div>

          <div className="mt-1 flex items-center justify-between text-[11px] text-neutral-400">
            <button
              onClick={() => setLoopQueue((v) => !v)}
              className={`rounded px-2 py-0.5 ${loopQueue ? "bg-white/15 text-neutral-100" : "hover:bg-white/10"}`}
            >
              🔁 Loop queue {loopQueue ? "on" : "off"}
            </button>
            <button
              onClick={() => setKeepAwake((v) => !v)}
              className={`rounded px-2 py-0.5 ${keepAwake ? "bg-white/15 text-neutral-100" : "hover:bg-white/10"}`}
              title="Stops the phone from auto-locking while music plays"
            >
              ☀ Screen awake {keepAwake ? "on" : "off"}
            </button>
            <button
              onClick={() => setVideoExpanded((v) => !v)}
              className="rounded px-2 py-0.5 hover:bg-white/10"
            >
              {videoExpanded ? "Shrink video" : "Expand video"}
            </button>
          </div>
        </div>
      </div>

      {/* Silent loop that anchors the media session to this page */}
      <audio ref={anchorRef} src="/music/silence.wav" loop preload="auto" />

      {/* Settings */}
      {showSettings && (
        <div
          className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 sm:items-center"
          onClick={() => setShowSettings(false)}
        >
          <div
            className="w-full max-w-md rounded-t-2xl border border-white/10 bg-neutral-900 p-5 sm:rounded-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-base font-semibold">Settings</h2>
            <label className="mt-4 block text-xs font-medium text-neutral-300">
              YouTube Data API key (optional)
              <input
                defaultValue={apiKey ?? ""}
                onBlur={(e) => {
                  const v = e.target.value.trim() || null;
                  setApiKey(v);
                  saveApiKey(v);
                }}
                placeholder="AIza…"
                className="mt-1 w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm outline-none placeholder:text-neutral-600"
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            <p className="mt-2 text-xs leading-relaxed text-neutral-400">
              Without a key, search uses public YouTube mirrors that can be
              flaky (pasting links always works). For reliable search, create a
              free key: Google Cloud Console → create project → enable
              &ldquo;YouTube Data API v3&rdquo; → Credentials → API key. The
              free quota covers ~100 searches/day and the key never leaves
              this browser.
            </p>
            <button
              onClick={() => setShowSettings(false)}
              className="mt-4 w-full rounded-lg bg-white py-2 text-sm font-semibold text-neutral-950"
            >
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
