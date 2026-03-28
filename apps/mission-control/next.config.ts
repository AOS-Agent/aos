import type { NextConfig } from "next";

const isTauriBuild = process.env.TAURI_ENV_PLATFORM !== undefined;

const nextConfig: NextConfig = {
  // Static export only for Tauri production builds
  // In dev mode, we need rewrites for API proxying
  ...(isTauriBuild
    ? { output: "export", distDir: "out" }
    : {}),
  images: {
    unoptimized: true,
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:4096/api/:path*",
      },
    ];
  },
};

export default nextConfig;
