import type { Metadata, Viewport } from "next";
import Player from "./player";

export const metadata: Metadata = {
  title: "TubePlay — YouTube as your music player",
  description:
    "Search any song on YouTube and play it like Spotify: queue, playlists, lock-screen controls, and background listening — free, right in your browser.",
  manifest: "/music/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    title: "TubePlay",
    statusBarStyle: "black-translucent",
  },
};

export const viewport: Viewport = {
  themeColor: "#0a0a0a",
};

export default function MusicPage() {
  return <Player />;
}
