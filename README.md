# Swing Coach

Upload or record a golf swing and get an instant, on-device scorecard:
pose tracking (MediaPipe), phase detection (P1/P4/P7), body-rotation and
ball-strike metrics, all graded against published biomechanics bands. Nothing
leaves the device.

## Run locally

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Deploy

This is a standard Next.js app at the repository root. On Vercel: import the
repo, Framework Preset = **Next.js**, Root Directory = **(blank)**. No
environment variables are required.

---

# TubePlay (`/music`)

A free "YouTube as your music player" web app that lives at `/music`:
search any song on YouTube (including live versions, extended intros, and
fan uploads that aren't on Spotify), then play it like a music app — queue,
saved playlists, seek bar, and lock-screen / media-key controls. Playback
uses the official YouTube embedded player, so views still count.

- **Search** works out of the box via public mirrors (can be flaky), or
  reliably with a free YouTube Data API v3 key entered in Settings (stored
  only in your browser). Pasting a YouTube link/ID always works.
- **Queue & playlists** are saved in `localStorage` — nothing leaves the
  browser.
- **Background listening:** on desktop just switch tabs/apps. On Android,
  Brave or Firefox with "background video playback" enabled keeps playing
  with the screen off (and you can Add to Home screen). On iPhone, lock the
  phone and hit play from the lock screen / Control Center to resume as
  audio.
