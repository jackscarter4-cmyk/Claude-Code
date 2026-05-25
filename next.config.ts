import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Fully static site (no server code) — exports to ./out, deploys anywhere.
  output: "export",
};

export default nextConfig;
