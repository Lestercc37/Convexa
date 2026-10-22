import type {
  ChainExpirationsResponse,
  FutureOpeningPriceResponse,
  GammaAggregateResponse,
  GammaHeatmapMetric,
  GammaHeatmapResponse,
  GammaHistoryResponse,
  GammaResponse,
  GammaView,
  MarketPriceHistoryResponse,
  MarketResponse,
  MinuteFlowResponse,
  OptionChainResponse,
  PriceCandlesResponse,
  ScreenerPresetName,
  ScreenerPresetResponse,
  ScreenerPresetSettings,
  ScreenerPresetSettingsListResponse,
  ScreenerPresetSettingsUpdate,
  UnderlyingsResponse,
  VwapHistoryResponse,
  CubeMinimumsResponse,
  WhaleAlertsResponse,
  WhaleThreshold,
  WhaleThresholdsResponse,
  WhaleThresholdUpdate,
} from "./types";
import { redirectToLoginOnUnauthorized } from "./session-redirect";

const API_PREFIX = "/backend/api/v1";

export class ApiError extends Error {
  readonly status: number;
  // The API's own error code (e.g. "OPENING_PRICE_NOT_OPEN_YET"), when the failed response carried one.
  readonly code: string | null;

  constructor(status: number, code: string | null = null) {
    super(`API request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

async function apiErrorFrom(response: Response, path: string): Promise<ApiError> {
  if (response.status === 401) redirectToLoginOnUnauthorized(path);
  let code: string | null = null;
  try {
    const body = (await response.json()) as { error?: { code?: unknown } };
    if (typeof body?.error?.code === "string") code = body.error.code;
  } catch {
    // no JSON body: the status alone is what we know
  }
  return new ApiError(response.status, code);
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, { cache: "no-store", signal });
  if (!response.ok) throw await apiErrorFrom(response, path);
  return (await response.json()) as T;
}

// The write paths in an otherwise read-only API client — see
// docs/use-cases.md's write-endpoint justification notes for why
// PATCH /whale-thresholds/{symbol} and PATCH /screener-preset-settings/{preset}
// are the exceptions.
async function patchJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw await apiErrorFrom(response, path);
  return (await response.json()) as T;
}

async function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw await apiErrorFrom(response, path);
  return (await response.json()) as T;
}

async function putJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw await apiErrorFrom(response, path);
  return (await response.json()) as T;
}

export type SessionUser = { username: string; is_admin: boolean };

export function login(username: string, password: string, signal?: AbortSignal) {
  return postJson<SessionUser>("/auth/login", { username, password }, signal);
}

export function logout(signal?: AbortSignal) {
  return postJson<{ ok: boolean }>("/auth/logout", {}, signal);
}

export function getMe(signal?: AbortSignal) {
  return getJson<SessionUser>("/auth/me", signal);
}

export function getInvitePreview(token: string, signal?: AbortSignal) {
  return getJson<{ username: string }>(`/auth/invites/${encodeURIComponent(token)}`, signal);
}

export function acceptInvite(token: string, password: string, signal?: AbortSignal) {
  return postJson<SessionUser>("/auth/accept-invite", { token, password }, signal);
}

export function getUnderlyings(signal?: AbortSignal) {
  return getJson<UnderlyingsResponse>("/underlyings", signal);
}

export function getGamma(symbol: string, view?: GammaView, signal?: AbortSignal) {
  const query = view ? `?${new URLSearchParams({ view }).toString()}` : "";
  return getJson<GammaResponse>(`/gamma/${encodeURIComponent(symbol)}${query}`, signal);
}

/**
 * Start of the window the Historical level traces cover: `sessions` weekdays back plus one extra day, at 00:00 UTC of that
 * date. The endpoint's default start is the beginning of time, which sent every stored row (8,436 rows / 2.8 MB for SPX
 * tactical, 2026-10-09) to draw a chart that shows a few sessions. Holidays are not subtracted: the window is only a bound,
 * a longer one is harmless.
 */
export function historyWindowStart(now: Date = new Date(), sessions = 5): Date {
  const day = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  let remaining = sessions;
  while (remaining > 0) {
    day.setUTCDate(day.getUTCDate() - 1);
    const weekday = day.getUTCDay();
    if (weekday !== 0 && weekday !== 6) remaining -= 1;
  }
  day.setUTCDate(day.getUTCDate() - 1);
  return day;
}

export function getGammaHistory(symbol: string, view?: GammaView, signal?: AbortSignal) {
  const params = new URLSearchParams({ start: historyWindowStart().toISOString() });
  if (view) params.set("view", view);
  return getJson<GammaHistoryResponse>(
    `/gamma/${encodeURIComponent(symbol)}/history?${params.toString()}`,
    signal,
  );
}

export function getPriceCandles(
  symbol: string,
  timeframe: PriceCandlesResponse["timeframe"],
  sessions = 5,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({ timeframe, sessions: String(sessions) });
  return getJson<PriceCandlesResponse>(`/market/${encodeURIComponent(symbol)}/candles?${params.toString()}`, signal);
}

export function getCubeMinimums(signal?: AbortSignal) {
  return getJson<CubeMinimumsResponse>("/alert-cubes/minimums", signal);
}

export function getMinuteFlow(symbol: string, options: { start?: Date }, signal?: AbortSignal) {
  const params = new URLSearchParams();
  if (options.start) params.set("start", options.start.toISOString());
  const query = params.toString() ? `?${params.toString()}` : "";
  return getJson<MinuteFlowResponse>(`/flow/${encodeURIComponent(symbol)}/minutes${query}`, signal);
}

export function getGammaHeatmap(
  symbol: string,
  options: { view?: GammaView; metric?: GammaHeatmapMetric; start?: Date; bucketSeconds?: number; source?: "cycle" | "fast" },
  signal?: AbortSignal,
) {
  const params = new URLSearchParams();
  if (options.view) params.set("view", options.view);
  if (options.metric) params.set("metric", options.metric);
  if (options.start) params.set("start", options.start.toISOString());
  if (options.bucketSeconds) params.set("bucket_seconds", String(options.bucketSeconds));
  // "fast" = the 10-second layer (gamma_fast_items); the default is the REST cycle's own rows.
  if (options.source) params.set("source", options.source);
  const query = params.toString() ? `?${params.toString()}` : "";
  return getJson<GammaHeatmapResponse>(`/gamma/${encodeURIComponent(symbol)}/heatmap${query}`, signal);
}

export function getGammaProfile(symbol: string, view?: GammaView, signal?: AbortSignal) {
  const query = view ? `?${new URLSearchParams({ view }).toString()}` : "";
  return getJson<GammaAggregateResponse>(
    `/gamma/${encodeURIComponent(symbol)}/profile${query}`,
    signal,
  );
}

export function getMarket(symbol: string, signal?: AbortSignal) {
  return getJson<MarketResponse>(`/market/${encodeURIComponent(symbol)}`, signal);
}

export function getMarketPriceHistory(symbol: string, signal?: AbortSignal) {
  return getJson<MarketPriceHistoryResponse>(
    `/market/${encodeURIComponent(symbol)}/history`,
    signal,
  );
}

export function getVwapHistory(symbol: string, signal?: AbortSignal) {
  return getJson<VwapHistoryResponse>(
    `/market/${encodeURIComponent(symbol)}/vwap-history`,
    signal,
  );
}

export function getAlerts(symbol: string, signal?: AbortSignal, limit?: number) {
  const query = limit ? `?${new URLSearchParams({ limit: String(limit) }).toString()}` : "";
  return getJson<WhaleAlertsResponse>(`/alerts/${encodeURIComponent(symbol)}${query}`, signal);
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

export function getOptionChainExpirations(symbol: string, signal?: AbortSignal) {
  return getJson<ChainExpirationsResponse>(
    `/chain/${encodeURIComponent(symbol)}/expirations`,
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

export function getFutureOpeningPrice(symbol: string, signal?: AbortSignal) {
  return getJson<FutureOpeningPriceResponse>(
    `/futures/${encodeURIComponent(symbol)}/opening-price`,
    signal,
  );
}

export function setFutureOpeningPrice(
  symbol: string,
  openingPrice: number,
  signal?: AbortSignal,
) {
  return putJson<FutureOpeningPriceResponse>(
    `/futures/${encodeURIComponent(symbol)}/opening-price`,
    { opening_price: openingPrice },
    signal,
  );
}

export function getScreenerPresetSettings(signal?: AbortSignal) {
  return getJson<ScreenerPresetSettingsListResponse>("/screener-preset-settings", signal);
}

export function updateScreenerPresetSettings(
  preset: string,
  update: ScreenerPresetSettingsUpdate,
  signal?: AbortSignal,
) {
  return patchJson<ScreenerPresetSettings>(
    `/screener-preset-settings/${encodeURIComponent(preset)}`,
    update,
    signal,
  );
}
