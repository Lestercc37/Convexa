// The stamp of the build this server is running (set in next.config.ts at build time). Open pages compare it with the stamp they were built
// with and reload themselves when it changed (lib/app-freshness.ts): a page left open for days otherwise never runs the fixes deployed meanwhile.
export const dynamic = "force-dynamic";

export function GET(): Response {
  return Response.json({ stamp: process.env.NEXT_PUBLIC_BUILD_STAMP ?? "" }, { headers: { "Cache-Control": "no-store" } });
}
