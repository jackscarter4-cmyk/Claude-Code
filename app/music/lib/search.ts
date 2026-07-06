import { type Track, thumbUrl } from "./types";

// This app is a fully static site, so search runs in the browser. Two paths:
//  1. YouTube Data API v3 (CORS-enabled by Google) when the user saved a key.
//  2. Keyless fallback via public Piped/Invidious instances. These come and go;
//     each is tried with a short timeout and the first success wins.
// A pasted URL / video id bypasses search entirely.

const PIPED_INSTANCES = [
  "https://pipedapi.kavin.rocks",
  "https://pipedapi.adminforge.de",
  "https://api.piped.private.coffee",
];

const INVIDIOUS_INSTANCES = [
  "https://inv.nadeko.net",
  "https://invidious.nerdvpn.de",
  "https://yewtu.be",
];

const VIDEO_ID_RE = /^[A-Za-z0-9_-]{11}$/;

/** Extract a video id from a pasted YouTube URL or bare id, else null. */
export function parseVideoInput(input: string): string | null {
  const s = input.trim();
  if (VIDEO_ID_RE.test(s)) return s;
  let url: URL;
  try {
    url = new URL(s);
  } catch {
    return null;
  }
  const host = url.hostname.replace(/^www\.|^m\./, "");
  if (host === "youtu.be") {
    const id = url.pathname.slice(1).split("/")[0];
    return VIDEO_ID_RE.test(id) ? id : null;
  }
  if (host === "youtube.com" || host === "music.youtube.com") {
    const v = url.searchParams.get("v");
    if (v && VIDEO_ID_RE.test(v)) return v;
    const m = url.pathname.match(/^\/(?:shorts|embed|live)\/([A-Za-z0-9_-]{11})/);
    if (m) return m[1];
  }
  return null;
}

function fetchWithTimeout(url: string, ms: number): Promise<Response> {
  return fetch(url, { signal: AbortSignal.timeout(ms) });
}

/** Titles from the Data API arrive HTML-entity-encoded. */
function decodeEntities(s: string): string {
  const el = document.createElement("textarea");
  el.innerHTML = s;
  return el.value;
}

function parseIsoDuration(iso: string): number | undefined {
  const m = iso.match(/^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$/);
  if (!m) return undefined;
  return (
    Number(m[1] ?? 0) * 3600 + Number(m[2] ?? 0) * 60 + Number(m[3] ?? 0)
  );
}

async function searchDataApi(query: string, apiKey: string): Promise<Track[]> {
  const searchUrl =
    "https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&maxResults=20" +
    `&q=${encodeURIComponent(query)}&key=${encodeURIComponent(apiKey)}`;
  const res = await fetchWithTimeout(searchUrl, 8000);
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const reason = body?.error?.message ?? `HTTP ${res.status}`;
    throw new Error(`YouTube API: ${reason}`);
  }
  const data = await res.json();
  const tracks: Track[] = (data.items ?? [])
    .filter((it: { id?: { videoId?: string } }) => it.id?.videoId)
    .map(
      (it: {
        id: { videoId: string };
        snippet: {
          title: string;
          channelTitle: string;
          thumbnails?: { medium?: { url?: string } };
        };
      }): Track => ({
        id: it.id.videoId,
        title: decodeEntities(it.snippet.title),
        channel: decodeEntities(it.snippet.channelTitle),
        thumb: it.snippet.thumbnails?.medium?.url ?? thumbUrl(it.id.videoId),
      }),
    );

  // One follow-up call fills in durations; search results don't include them.
  if (tracks.length > 0) {
    try {
      const ids = tracks.map((t) => t.id).join(",");
      const vres = await fetchWithTimeout(
        `https://www.googleapis.com/youtube/v3/videos?part=contentDetails&id=${ids}&key=${encodeURIComponent(apiKey)}`,
        8000,
      );
      if (vres.ok) {
        const vdata = await vres.json();
        const durs = new Map<string, number | undefined>(
          (vdata.items ?? []).map(
            (v: { id: string; contentDetails?: { duration?: string } }) => [
              v.id,
              v.contentDetails?.duration
                ? parseIsoDuration(v.contentDetails.duration)
                : undefined,
            ],
          ),
        );
        for (const t of tracks) t.durationSec = durs.get(t.id);
      }
    } catch {
      // durations are cosmetic; ignore
    }
  }
  return tracks;
}

async function searchPiped(base: string, query: string): Promise<Track[]> {
  const res = await fetchWithTimeout(
    `${base}/search?q=${encodeURIComponent(query)}&filter=videos`,
    5000,
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  const items = (data.items ?? []).filter(
    (it: { url?: string }) => it.url?.startsWith("/watch?v="),
  );
  const tracks = items.map(
    (it: {
      url: string;
      title: string;
      uploaderName?: string;
      duration?: number;
    }): Track => {
      const id = new URLSearchParams(it.url.split("?")[1]).get("v") ?? "";
      return {
        id,
        title: it.title,
        channel: it.uploaderName ?? "",
        durationSec: it.duration && it.duration > 0 ? it.duration : undefined,
        thumb: thumbUrl(id),
      };
    },
  );
  if (tracks.length === 0) throw new Error("empty result");
  return tracks;
}

async function searchInvidious(base: string, query: string): Promise<Track[]> {
  const res = await fetchWithTimeout(
    `${base}/api/v1/search?q=${encodeURIComponent(query)}&type=video`,
    5000,
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  const tracks = (Array.isArray(data) ? data : [])
    .filter((it: { videoId?: string }) => it.videoId)
    .map(
      (it: {
        videoId: string;
        title: string;
        author?: string;
        lengthSeconds?: number;
      }): Track => ({
        id: it.videoId,
        title: it.title,
        channel: it.author ?? "",
        durationSec:
          it.lengthSeconds && it.lengthSeconds > 0
            ? it.lengthSeconds
            : undefined,
        thumb: thumbUrl(it.videoId),
      }),
    );
  if (tracks.length === 0) throw new Error("empty result");
  return tracks;
}

export async function searchYouTube(
  query: string,
  apiKey: string | null,
): Promise<Track[]> {
  if (apiKey) return searchDataApi(query, apiKey);

  try {
    return await Promise.any(
      PIPED_INSTANCES.map((b) => searchPiped(b, query)),
    );
  } catch {
    // fall through to Invidious
  }
  try {
    return await Promise.any(
      INVIDIOUS_INSTANCES.map((b) => searchInvidious(b, query)),
    );
  } catch {
    throw new Error(
      "Keyless search is unavailable right now (public mirrors are down). " +
        "Paste a YouTube link directly, or add a free YouTube API key in Settings for reliable search.",
    );
  }
}

/** Best-effort title lookup for a pasted link (noembed proxies YouTube oEmbed with CORS). */
export async function lookupTitle(
  id: string,
): Promise<{ title: string; channel: string } | null> {
  try {
    const res = await fetchWithTimeout(
      `https://noembed.com/embed?url=${encodeURIComponent(`https://www.youtube.com/watch?v=${id}`)}`,
      5000,
    );
    if (!res.ok) return null;
    const data = await res.json();
    if (!data.title) return null;
    return { title: data.title, channel: data.author_name ?? "" };
  } catch {
    return null;
  }
}
