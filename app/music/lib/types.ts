export type Track = {
  /** 11-character YouTube video id */
  id: string;
  title: string;
  channel: string;
  /** Seconds; unknown for pasted links until the player loads them */
  durationSec?: number;
  thumb: string;
};

export type Playlist = {
  name: string;
  tracks: Track[];
  updatedAt: number;
};

export function thumbUrl(id: string): string {
  return `https://i.ytimg.com/vi/${id}/mqdefault.jpg`;
}

export function formatDuration(sec: number | undefined): string {
  if (sec === undefined || !isFinite(sec) || sec <= 0) return "";
  const s = Math.round(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`
    : `${m}:${String(r).padStart(2, "0")}`;
}
