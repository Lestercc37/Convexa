import type {
  GammaAggregateResponse,
  GammaHistoryResponse,
  GammaResponse,
  MarketResponse,
  OptionChainResponse,
  ScreenerPresetName,
  ScreenerPresetResponse,
  UnderlyingsResponse,
  WhaleAlertsResponse,
  WhaleThreshold,
  WhaleThresholdsResponse,
  WhaleThresholdUpdate,
} from "./types";

const API_PREFIX = "/backend/api/v1";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`API request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, { cache: "no-store", signal });
  if (!response.ok) throw new ApiError(response.status);
  return (await response.json()) as T;
}

// The one write path in an otherwise read-only API client — see
// docs/use-cases.md's "único endpoint de escritura" note for why
// PATCH /whale-thresholds/{symbol} is the sole exception.
async function patchJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw new ApiError(response.status);
  return (await response.json()) as T;
}

export function getUnderlyings(signal?: AbortSignal) {
  return getJson<UnderlyingsResponse>("/underlyings", signal);
}

export function getGamma(symbol: string, signal?: AbortSignal) {
  return getJson<GammaResponse>(`/gamma/${encodeURIComponent(symbol)}`, signal);
}

export function getGammaHistory(symbol: string, signal?: AbortSignal) {
  return getJson<GammaHistoryResponse>(
    `/gamma/${encodeURIComponent(symbol)}/history`,
    signal,
  );
}

export function getGammaProfile(symbol: string, signal?: AbortSignal) {
  return getJson<GammaAggregateResponse>(
    `/gamma/${encodeURIComponent(symbol)}/profile`,
    signal,
  );
}

export function getMarket(symbol: string, signal?: AbortSignal) {
  return getJson<MarketResponse>(`/market/${encodeURIComponent(symbol)}`, signal);
}

export function getAlerts(symbol: string, signal?: AbortSignal) {
  return getJson<WhaleAlertsResponse>(`/alerts/${encodeURIComponent(symbol)}`, signal);
}

export function getScreenerPreset(
  preset: ScreenerPresetName,
  signal?: AbortSignal,
) {
  return getJson<ScreenerPresetResponse>(
    `/screener-presets/${encodeURIComponent(preset)}`,
    signal,
  );
}

export function getOptionChain(
  symbol: string,
  expiration?: string,
  signal?: AbortSignal,
) {
  const query = expiration
    ? `?${new URLSearchParams({ expiration }).toString()}`
    : "";
  return getJson<OptionChainResponse>(
    `/chain/${encodeURIComponent(symbol)}${query}`,
    signal,
  );
}

export function getWhaleThresholds(signal?: AbortSignal) {
  return getJson<WhaleThresholdsResponse>("/whale-thresholds", signal);
}

export function updateWhaleThreshold(
  symbol: string,
  update: WhaleThresholdUpdate,
  signal?: AbortSignal,
) {
  return patchJson<WhaleThreshold>(
    `/whale-thresholds/${encodeURIComponent(symbol)}`,
    update,
    signal,
  );
}
