import type { NextConfig } from "next";

const apiUrl = process.env.CONVEXA_API_URL;

const nextConfig: NextConfig = {
  async rewrites() {
    if (!apiUrl) return [];
    return [{ source: "/backend/:path*", destination: `${apiUrl}/:path*` }];
  },
};

export default nextConfig;
