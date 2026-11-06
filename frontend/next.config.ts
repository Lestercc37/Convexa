import type { NextConfig } from "next";

const apiUrl = process.env.CONVEXA_API_URL;
// Stamped into the client and the /build-stamp route at build time: open pages compare the two and reload when a new build is live
// (lib/app-freshness.ts).
const buildStamp = process.env.NEXT_PUBLIC_BUILD_STAMP ?? new Date().toISOString();

const nextConfig: NextConfig = {
  env: { NEXT_PUBLIC_BUILD_STAMP: buildStamp },
  // Next.js's dev server only trusts localhost/127.0.0.1 by default and
  // 403s everything else (confirmed live: a JS chunk request with
  // Origin: http://100.115.162.74:3000 returned 403 "Unauthorized") — the
  // page HTML itself isn't origin-checked, so it loads fine while every
  // asset fails, which is why the page appeared to load but do nothing.
  allowedDevOrigins: ["100.115.162.74"],
  async rewrites() {
    if (!apiUrl) return [];
    return [{ source: "/backend/:path*", destination: `${apiUrl}/:path*` }];
  },
};

export default nextConfig;
