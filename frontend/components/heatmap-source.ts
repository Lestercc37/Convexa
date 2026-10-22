import { getGammaHeatmap } from "@/lib/api";
import type { GammaHeatmapMetric, GammaView } from "@/lib/types";
import { type HeatmapData, heatmapFromResponse, prependHistory } from "./gamma-heatmap-primitive";

// The heatmap's data source (docs/diseno-capa-rapida-gamma-2026-10-19.txt): the 10-second fast layer when the backend has one running, the REST cycle's one-minute columns otherwise.
// The fast layer is asked first; when it has nothing (switched off, not started yet, a symbol it does not cover, any error) the cycle's columns are used exactly as before, and the fast
// layer is not asked again for that symbol for a minute so a switched-off layer costs one extra request a minute, not one per poll.
const CYCLE_BUCKET_SECONDS = 60;
const FAST_BUCKET_SECONDS = 10;
// NDX has ~150 strikes in its tactical window: at 10 s a full session is ~349k rows, past the endpoint's 200k-row cap, so it is asked at 30 s (~117k).
const FAST_BUCKET_SECONDS_BY_SYMBOL: Record<string, number> = { NDX: 30 };
const FAST_RETRY_AFTER_MS = 60_000;
// The cycle's earlier columns are re-read at most this often while the fast layer is live (they only grow by a minute at a time).
const HISTORY_REFRESH_MS = 60_000;
// Incremental polls (2026-10-22, measured: every 10 s poll re-sent the WHOLE session, NDX 2.15 MB / 1.19 s of API CPU per poll, 13 % of a core per viewer at the end of the day): after the first full
// request the fast layer is asked only from the BUCKET of the last column it already has (the server's buckets have a fixed origin, so the tail lines up) and merged into what is held. A full request
// again every FAST_FULL_REFRESH_MS re-syncs anything the tail cannot carry (a revised earlier column, the day's colour scale, a strike that stopped being listed).
const FAST_FULL_REFRESH_MS = 5 * 60_000;
const FAST_CACHE_KEEP_MS = 12 * 60 * 60_000;

export const HEATMAP_FAST_POLLING_INTERVAL_MS = 10_000;

type HeatmapRequest = { view: GammaView; metric: GammaHeatmapMetric; start: Date };
export type LoadedHeatmap = { data: HeatmapData; fast: boolean };

const fastUnavailableUntil = new Map<string, number>();
const historyCache = new Map<string, { at: number; data: HeatmapData }>();
// The merged fast matrix of each (symbol, view, metric, start), with when it was last fetched IN FULL.
const fastCache = new Map<string, { data: HeatmapData; fullAt: number }>();

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export function fastBucketSeconds(symbol: string): number {
  return FAST_BUCKET_SECONDS_BY_SYMBOL[symbol.toUpperCase()] ?? FAST_BUCKET_SECONDS;
}

/** Test hook: forget what was learned about which symbols have a fast layer. */
export function resetHeatmapSourceState(): void {
  fastUnavailableUntil.clear();
  historyCache.clear();
  fastCache.clear();
}

export async function loadHeatmap(symbol: string, request: HeatmapRequest, signal?: AbortSignal): Promise<LoadedHeatmap> {
  const key = `${symbol}|${request.view}|${request.metric}|${request.start.getTime()}`;
  const retryAt = fastUnavailableUntil.get(symbol) ?? 0;
  if (Date.now() >= retryAt) {
    try {
      const now = Date.now();
      const held = fastCache.get(key);
      const lastColumn = held?.data.columns.at(-1);
      const incremental = held !== undefined && lastColumn !== undefined && now - held.fullAt < FAST_FULL_REFRESH_MS;
      const response = await getGammaHeatmap(
        symbol,
        {
          ...request,
          // From the start of the last column's bucket (its time is already floored to the bucket), so that column is refreshed and the tail lines up.
          start: incremental ? new Date(lastColumn.time * 1000) : request.start,
          bucketSeconds: fastBucketSeconds(symbol),
          source: "fast",
        },
        signal,
      );
      if (response.columns.length) {
        const tail = heatmapFromResponse(response);
        const live = incremental ? prependHistory(held.data, tail) : tail;
        for (const [otherKey, entry] of fastCache) if (now - entry.fullAt > FAST_CACHE_KEEP_MS) fastCache.delete(otherKey);
        fastCache.set(key, { data: live, fullAt: incremental ? held.fullAt : now });
        return { data: prependHistory(await cycleHistory(key, symbol, request, signal), live), fast: true };
      }
    } catch (error) {
      if (isAbort(error)) throw error;
    }
    fastUnavailableUntil.set(symbol, Date.now() + FAST_RETRY_AFTER_MS);
  }
  const response = await getGammaHeatmap(symbol, { ...request, bucketSeconds: CYCLE_BUCKET_SECONDS }, signal);
  return { data: heatmapFromResponse(response), fast: false };
}

async function cycleHistory(
  key: string,
  symbol: string,
  request: HeatmapRequest,
  signal?: AbortSignal,
): Promise<HeatmapData | null> {
  const cached = historyCache.get(key);
  if (cached && Date.now() - cached.at < HISTORY_REFRESH_MS) return cached.data;
  try {
    const response = await getGammaHeatmap(symbol, { ...request, bucketSeconds: CYCLE_BUCKET_SECONDS }, signal);
    const data = heatmapFromResponse(response);
    historyCache.set(key, { at: Date.now(), data });
    return data;
  } catch (error) {
    if (isAbort(error)) throw error;
    // A failed history read keeps the last one (or none): the fast columns still draw.
    return cached?.data ?? null;
  }
}
