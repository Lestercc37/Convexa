import type { GammaResponse, MarketResponse, UnderlyingsResponse } from "./types";

const API_PREFIX = "/backend/api/v1";

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, { cache: "no-store", signal });
  if (!response.ok) throw new Error(`API request failed (${response.status})`);
  return (await response.json()) as T;
}

export function getUnderlyings(signal?: AbortSignal) {
  return getJson<UnderlyingsResponse>("/underlyings", signal);
}

export function getGamma(symbol: string, signal?: AbortSignal) {
  return getJson<GammaResponse>(`/gamma/${encodeURIComponent(symbol)}`, signal);
}

export function getMarket(symbol: string, signal?: AbortSignal) {
  return getJson<MarketResponse>(`/market/${encodeURIComponent(symbol)}`, signal);
}
