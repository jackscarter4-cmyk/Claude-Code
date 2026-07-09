import type { Playlist, Track } from "./types";

// Everything lives in localStorage — the site is static and nothing leaves
// the browser except the YouTube/search requests themselves.

const KEYS = {
  apiKey: "tubeplay.apiKey",
  queue: "tubeplay.queue",
  queueIndex: "tubeplay.queueIndex",
  playlists: "tubeplay.playlists",
  history: "tubeplay.history",
} as const;

function read<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // storage full or blocked — non-fatal
  }
}

export const loadApiKey = () => read<string | null>(KEYS.apiKey, null);
export const saveApiKey = (key: string | null) =>
  key
    ? write(KEYS.apiKey, key)
    : window.localStorage.removeItem(KEYS.apiKey);

export const loadQueue = () => read<Track[]>(KEYS.queue, []);
export const saveQueue = (q: Track[]) => write(KEYS.queue, q);

export const loadQueueIndex = () => read<number>(KEYS.queueIndex, 0);
export const saveQueueIndex = (i: number) => write(KEYS.queueIndex, i);

export const loadPlaylists = () => read<Playlist[]>(KEYS.playlists, []);
export const savePlaylists = (p: Playlist[]) => write(KEYS.playlists, p);

const HISTORY_MAX = 50;

export const loadHistory = () => read<Track[]>(KEYS.history, []);

export function pushHistory(track: Track): Track[] {
  const rest = loadHistory().filter((t) => t.id !== track.id);
  const next = [track, ...rest].slice(0, HISTORY_MAX);
  write(KEYS.history, next);
  return next;
}
