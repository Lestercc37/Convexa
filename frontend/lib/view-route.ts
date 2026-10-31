import type { Timeframe } from "./candles";
import type { GammaHeatmapMetric, GammaView } from "./types";
import { APP_GAMMA_VIEW_TABS, GAMMA_VIEW_TABS } from "./gamma-views";

/**
 * One route per view (desktop app design, docs/diseno-app-escritorio-2026-10-26.txt section 5): /vista/grafico carries everything a window needs
 * in its address, so the desktop app (or a browser tab) can open "SPX, 1m, Heatmap Delta" directly:
 *   /vista/grafico?simbolo=SPX&tf=1m&rango=dia&dte=0dte&capa=heatmap-delta
 * Without `capa` the chart is plain (candles, EMAs, VWAP and the level lines, no layer): the app's main window. With `capa` the chart shows that one
 * layer: the app's window for that layer (Lester 2026-10-29, "one window per layer, never a second full dashboard").
 */
export const CHART_LAYERS = [
  "heatmap-gamma",
  "heatmap-abs",
  "heatmap-total",
  "heatmap-delta",
  "heatmap-vanna",
  "heatmap-charm",
  "heatmap-calls",
  "heatmap-puts",
  "profile",
  "smile",
  "flow",
  "cubes",
] as const;
export type ChartLayer = (typeof CHART_LAYERS)[number];

export type ChartRouteState = {
  symbol: string | null;
  timeframe: Timeframe | null;
  range: "day" | "week" | null;
  gammaTab: GammaView | null;
  layer: ChartLayer | null;
};

/** What PriceChart starts with: which of its layers are on. Absent = the web dashboard's own defaults (profile and Smile on, the rest off). */
export type ChartLayerPreset = {
  profile: boolean;
  smile: boolean;
  flow: boolean;
  cubes: boolean;
  heatmap: GammaHeatmapMetric | "calls" | "puts" | null;
};

const TIMEFRAMES: readonly Timeframe[] = ["1m", "5m", "15m", "1h"];
const HEATMAP_METRIC_BY_LAYER: Record<string, GammaHeatmapMetric | "calls" | "puts"> = {
  "heatmap-gamma": "gamma",
  "heatmap-abs": "absolute_gamma",
  "heatmap-total": "total_gamma",
  "heatmap-delta": "delta",
  "heatmap-vanna": "vanna",
  "heatmap-charm": "charm",
  "heatmap-calls": "calls",
  "heatmap-puts": "puts",
};

export function parseChartRoute(params: URLSearchParams): ChartRouteState {
  const symbol = params.get("simbolo")?.trim().toUpperCase() || null;
  const tf = params.get("tf");
  const range = params.get("rango");
  const dte = params.get("dte");
  const layer = params.get("capa");
  return {
    symbol: symbol && /^[A-Z0-9.^/]{1,12}$/.test(symbol) ? symbol : null,
    timeframe: TIMEFRAMES.includes(tf as Timeframe) ? (tf as Timeframe) : null,
    range: range === "dia" ? "day" : range === "semana" ? "week" : null,
    gammaTab: [...GAMMA_VIEW_TABS, ...APP_GAMMA_VIEW_TABS].some((tab) => tab.view === dte) ? (dte as GammaView) : null,
    layer: CHART_LAYERS.includes(layer as ChartLayer) ? (layer as ChartLayer) : null,
  };
}

export function chartRouteQuery(state: ChartRouteState): string {
  const params = new URLSearchParams();
  if (state.symbol) params.set("simbolo", state.symbol);
  if (state.timeframe) params.set("tf", state.timeframe);
  if (state.range) params.set("rango", state.range === "day" ? "dia" : "semana");
  if (state.gammaTab) params.set("dte", state.gammaTab);
  if (state.layer) params.set("capa", state.layer);
  return params.toString();
}

export function layerPreset(layer: ChartLayer | null): ChartLayerPreset {
  return {
    profile: layer === "profile",
    smile: layer === "smile",
    flow: layer === "flow",
    cubes: layer === "cubes",
    heatmap: layer ? (HEATMAP_METRIC_BY_LAYER[layer] ?? null) : null,
  };
}
