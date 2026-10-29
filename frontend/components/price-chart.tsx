"use client";

import Image from "next/image";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  LineType,
  TickMarkType,
  type AutoscaleInfo,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { getAlerts, getGammaHistory, getGammaProfile, getMinuteFlow, getOptionChain, getOptionChainExpirations, getPriceCandles, getCubeMinimums } from "@/lib/api";
import { flowBarsFromResponse } from "@/lib/minute-flow";
import { buildCubes, DEFAULT_CUBE_FILTER, DEFAULT_CUBE_SIZING, type CubeSizing } from "@/lib/alert-cubes";
import { CUBE_MIN_LIMIT, effectiveCubeMin, loadCubeMins, loadCubeSizing, parseCubeMin, saveCubeMins, saveCubeSizing } from "@/lib/cube-minimum";
import { aggregateVwapPoints, type MinuteCandle, type Timeframe, type VwapPoint } from "@/lib/candles";
import { recordChartEvent } from "@/lib/chart-lifecycle";
import { useLanguage } from "@/lib/i18n/language-context";
import type { MarketPriceStreamStatus } from "@/lib/market-price-stream";
import { currentEasternDate, EASTERN_TIME_ZONE, heatmapStartSeconds, mostRecentSessionRange } from "@/lib/market-session";
import { formatDeltaFlow, formatPremium, summarizeOpeningAlerts, summarizeOpeningFlow, type OpeningAlerts, type OpeningFlow } from "@/lib/opening-flow";
import { emaValues, loadEmas, saveEmas, type EmaConfig } from "@/lib/ema";
import type {
  AtrRange,
  ExpectedMove,
  GammaHeatmapMetric,
  GammaHistoryItem,
  GammaResponse,
  GammaView,
  WhaleAlert,
} from "@/lib/types";
import { LEVEL_MERGE_THRESHOLD } from "./gravity-map";
import { AlertCubesPrimitive } from "./alert-cubes-primitive";
import { EmaMenu } from "./ema-menu";
import { CandleCountdownPrimitive } from "./candle-countdown-primitive";
import { candleSecondsLeft, formatCountdown } from "@/lib/candle-countdown";
import { AlertCubeTooltip } from "./alert-cube-tooltip";
import { HEATMAP_FAST_POLLING_INTERVAL_MS, loadHeatmap } from "./heatmap-source";
import { GammaHeatmapPrimitive, mergeSides, paletteForMetric, type HeatmapData } from "./gamma-heatmap-primitive";
import {
  LiveProfilePrimitive,
  profileBarsFromHeatmap,
  profileWidthCssPx,
  sideBarsFromHeatmaps,
  type ProfileBar,
  type SmilePoint,
} from "./live-profile-primitive";
import { LIVE_LEVELS_POLLING_INTERVAL_MS, POLLING_INTERVAL_MS } from "@/lib/polling";
import { currentExpirations, smilePointsFromChain } from "@/lib/volatility-smile-data";

type PriceChartProps = {
  symbol: string;
  candles: MinuteCandle[];
  gamma: GammaResponse;
  gammaView?: GammaView;
  vwapPoints?: VwapPoint[];
  vwapNotApplicable?: boolean;
  vwapProxySymbol?: string | null;
  atrRange?: AtrRange;
  expectedMove?: ExpectedMove;
  timeframe?: Timeframe;
  // The forming candle's countdown under the last price (default on; the week view has no forming intraday candle).
  candleCountdown?: boolean;
  streamStatus?: MarketPriceStreamStatus;
};

type GammaLevel = {
  // null is a legitimate gamma_flip (no sign crossing found in the
  // current window, not an error) -- every consumer of this price must
  // skip the level rather than pass null into a lightweight-charts call,
  // which asserts its price/value arguments are numbers and throws
  // otherwise. This is containment only, not the final "no crossing"
  // display design.
  price: number | null;
  title: string;
  color: string;
};

type LevelMode = "static" | "historical";

type BandRect = {
  top: number;
  height: number;
};

// Convexa brand colors (matches --calm/--risk in globals.css) — reserved for
// levels Convexa itself calculates. Candlesticks below intentionally use a
// separate, TradingView-native pair (see CANDLE_UP_COLOR/CANDLE_DOWN_COLOR).
const CONVEXA_GREEN = "#00DC5A";
const CONVEXA_RED = "#FA000A";

const HISTORICAL_LEVELS = [
  { field: "call_wall", title: "Call Wall", color: CONVEXA_GREEN },
  { field: "gamma_flip", title: "Gamma Flip", color: "#f3c969" },
  { field: "put_wall", title: "Put Wall", color: CONVEXA_RED },
] as const;

const VWAP_COLOR = "#f3c969";

// How long after mount the [candles] effect keeps re-fitting on every bulk
// jump, instead of only the first one -- confirmed live, 2026-09-21: under a
// slow/retried initial history load (ThetaData latency degraded post-P1
// rollout), `candles` can jump by >1 more than once in quick succession as
// partial responses land and get superseded, and the old "exactly once,
// ever" guard could consume its one re-fit against an early, still-partial
// jump, leaving the chart permanently under-fit even once the rest of the
// day's candles arrived seconds later. 15s comfortably covers the slow
// loads observed live (8-10s) with headroom, while still turning itself off
// well before steady-state polling would let this re-fit start fighting a
// user's own pan/zoom.
const CANDLE_SEED_SETTLING_WINDOW_MS = 15_000;

// With only a few candles (the first minutes of the session, a symbol that just loaded) fitContent() stretches them over the whole width and they come out huge ("spread out", Lester 2026-10-21).
// After every fit the live spacing is measured and, when it is wider than this, the candles keep this width and start from the CENTER of the chart (his request): the first candle sits in the middle
// of the candle area (the pane minus the profile column) and the new ones grow to the right; once they reach the right edge the last one stays there.
export const MAX_FIT_BAR_SPACING_PX = 10;

/**
 * The visible logical range for the few-candles case, or null when the spacing of the current fit is already fine. `range` is what fitContent() left (first bar at `range.from`, the strip reserved on the
 * right counted in `range.to`), `width` the whole pane and `reservedPx` the strip. The candle area is the pane without the strip: with n bars of `maxSpacing` px, the first bar goes to the middle of that area
 * while the n bars fit in its right half, and from then on the last bar stays at its right edge.
 */
export function cappedRange(
  range: { from: number; to: number } | null,
  width: number,
  reservedPx = 0,
  maxSpacing: number = MAX_FIT_BAR_SPACING_PX,
): { from: number; to: number } | null {
  if (!range || !(width > 0)) return null;
  const bars = range.to - range.from;
  if (!(bars > 0) || width / bars <= maxSpacing) return null;
  const candleArea = width - reservedPx;
  if (!(candleArea > 0)) return null;
  const lastIndex = range.to - reservedPx / (width / bars);
  const count = lastIndex - range.from + 1;
  const from = count * maxSpacing <= candleArea / 2 ? range.from - candleArea / 2 / maxSpacing : lastIndex - candleArea / maxSpacing;
  return { from, to: from + width / maxSpacing };
}

/** fitContent() now, then (the library applies the new spacing a moment later) the spacing cap; the measured numbers go to the flight recorder (convexaChartLog) so the fit can be checked numerically. */
function fitCandles(chart: IChartApi, stillWanted: () => boolean, reservedPx: () => number): void {
  chart.timeScale().fitContent();
  window.setTimeout(() => {
    if (!stillWanted()) return;
    try {
      const timeScale = chart.timeScale();
      const range = timeScale.getVisibleLogicalRange();
      const width = timeScale.width();
      const next = cappedRange(range, width, reservedPx());
      recordChartEvent("fit", { bars: range ? Math.round((range.to - range.from) * 10) / 10 : null, spacing: range && width > 0 ? Math.round((width / (range.to - range.from)) * 10) / 10 : null, width, capped: next !== null });
      if (next) timeScale.setVisibleLogicalRange(next);
    } catch {
      // the chart was removed between the fit and the check: nothing to cap
    }
  }, 80);
}

// Neutral, distinct from every color already in use on this chart (candles,
// Gamma levels, VWAP, ATR bands) — a user-drawn annotation, not something
// Convexa calculated.
const TRENDLINE_COLOR = "#e6e6e6";

type TrendlinePoint = { time: UTCTimestamp; price: number };
type Trendline = { start: TrendlinePoint; end: TrendlinePoint };

function gammaLevels(gamma: GammaResponse): GammaLevel[] {
  // The honest-empty Tactical case (no chain data fetched at all -- see
  // GammaView's own comment in lib/types.ts) -- every field below still
  // comes back a real 0/null in that case,
  // which would otherwise draw Call Wall/Put Wall/Gamma Flip pinned at
  // price 0 as if they were genuine levels. See GammaResponse.has_data's
  // own comment; the empty-state message this produces is rendered
  // separately (see the levelMode==="static" effect below).
  if (!gamma.has_data) return [];
  // call_wall/put_wall are null when CalculateWallsUseCase found no
  // valid candidate on that side (e.g. every in-range strike nets the
  // same sign) -- a real, distinct outcome from "the wall is at strike
  // 0", same coordinated null-handling as gamma_flip. range can only be
  // computed with both walls present; the merge check below is simply
  // skipped otherwise (each level still renders on its own, or is
  // hidden individually if its own price is null -- see the returned
  // array below).
  const range =
    gamma.call_wall !== null && gamma.put_wall !== null
      ? gamma.call_wall - gamma.put_wall
      : null;
  const mergeFlipAndAbsolute =
    gamma.gamma_flip !== null &&
    range !== null &&
    range > 0 &&
    Math.abs(gamma.gamma_flip - gamma.absolute_gamma_strike) <
      LEVEL_MERGE_THRESHOLD * range;
  const middleLevels = mergeFlipAndAbsolute
    ? [{ price: gamma.gamma_flip, title: "Flip / Abs. Gamma", color: "#f3c969" }]
    : [
        { price: gamma.gamma_flip, title: "Gamma Flip", color: "#f3c969" },
        { price: gamma.absolute_gamma_strike, title: "Abs. Gamma", color: "#7eb6ff" },
      ];

  return [
    { price: gamma.put_wall, title: "Put Wall", color: CONVEXA_RED },
    ...middleLevels,
    { price: gamma.call_wall, title: "Call Wall", color: CONVEXA_GREEN },
  ];
}

// EMC/EMP -- Expected Move Call / Expected Move Put, same call/put naming
// *and color* convention as Call Wall/Put Wall (green/red) so both level
// pairs read the same way, even though these come from a completely
// different calculation (ATM straddle IV off the chain's nearest
// expiration, not dealer OI/gamma). Degenerate (upper_bound ===
// lower_bound, e.g. atm_iv genuinely 0 -- see calculate_bsm_greeks.py's
// own IV=0 watch item) returns [] rather than two lines sitting exactly
// on top of each other at spot.
function expectedMoveLevels(expectedMove: ExpectedMove | undefined): GammaLevel[] {
  if (!expectedMove) return [];
  const { upper_bound, lower_bound } = expectedMove;
  if (upper_bound <= lower_bound) return [];
  return [
    { price: upper_bound, title: "EMC", color: CONVEXA_GREEN },
    { price: lower_bound, title: "EMP", color: CONVEXA_RED },
  ];
}

function chartCandle(candle: MinuteCandle) {
  return { ...candle, time: candle.time as UTCTimestamp };
}

// lightweight-charts formats every axis tick mark from the *UTC* digits of
// the given timestamp (confirmed by reading its own source -- it builds a
// Date from getUTCHours()/getUTCMinutes()/etc. and locale-formats that,
// never the browser's local timezone). MinuteCandle.time and the session
// anchor above are both real UTC epoch seconds, so left alone, a candle
// at the real moment 15:59 ET (EDT, UTC-4) prints as "19:59" -- the axis
// showing extended-hours-looking labels for perfectly in-session data,
// confirmed live, 2026-09. Intl.DateTimeFormat with an explicit
// `timeZone` resolves the correct ET offset for whatever date is given
// (EDT or EST), so this holds across the DST transition without
// hardcoding either offset.
function tickMarkFormatter(timeSeconds: UTCTimestamp, tickMarkType: TickMarkType, locale: string): string {
  const date = new Date(timeSeconds * 1000);
  const options: Intl.DateTimeFormatOptions = { timeZone: EASTERN_TIME_ZONE };
  switch (tickMarkType) {
    case TickMarkType.Year:
      options.year = "numeric";
      break;
    case TickMarkType.Month:
      options.month = "short";
      break;
    case TickMarkType.DayOfMonth:
      options.day = "numeric";
      break;
    case TickMarkType.Time:
      options.hour12 = false;
      options.hour = "2-digit";
      options.minute = "2-digit";
      break;
    case TickMarkType.TimeWithSeconds:
      options.hour12 = false;
      options.hour = "2-digit";
      options.minute = "2-digit";
      options.second = "2-digit";
      break;
  }
  return new Intl.DateTimeFormat(locale, options).format(date);
}

// The crosshair's own time label (top of the chart, follows the mouse)
// has no relation to tickMarkFormatter above -- lightweight-charts calls
// a *separate* localization.timeFormatter for it, which defaults to
// UTC-based formatting if left unset (confirmed live, 2026-09-26: the
// axis already read ET correctly, the crosshair label next to it still
// read UTC). Same EASTERN_TIME_ZONE conversion, just always full
// date+time since the crosshair label doesn't get the axis's own
// zoom-dependent granularity to fall back on.

function crosshairTimeFormatter(timeSeconds: UTCTimestamp): string {
  const date = new Date(timeSeconds * 1000);
  return new Intl.DateTimeFormat(undefined, {
    timeZone: EASTERN_TIME_ZONE,
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

// Prepends a whitespace point (a bar with only a `time`, no OHLC values --
// lightweight-charts renders nothing for it) one second before the
// session's 09:30 ET open. Confirmed live against the real library: a
// *leading* whitespace point correctly anchors fitContent()'s left edge
// at the open even with very few real candles, so early in the day the
// chart still reads "from market open," not "from whichever few minutes
// happen to exist" -- a *trailing* anchor at 16:00 does NOT work the same
// way (lightweight-charts trims trailing whitespace back to the last
// real bar regardless of API used -- setVisibleRange/setVisibleLogicalRange
// both confirmed to do this), so this can only pin the start of the
// session, not pre-render empty space out to the close while the market
// is still open.
function withSessionOpenAnchor(
  candles: MinuteCandle[],
  sessionOpenSeconds: number,
): (ReturnType<typeof chartCandle> | { time: UTCTimestamp })[] {
  // No real candle yet (e.g. right after a symbol switch, before the seed
  // fetch lands) -- never hand the series an array containing *only* the
  // whitespace anchor. Confirmed live, 2026-09-22: a real "Value is null"
  // crash inside lightweight-charts' own bar-styling code, reproduced when
  // a gamma/ATR update fired setData() while `candles` was still empty,
  // leaving the anchor as the sole entry. A whitespace point exists to
  // pin fitContent()'s left edge next to *real* bars -- with none, there's
  // nothing for it to anchor anyway.
  if (candles.length === 0) return [];
  // A multi-session series (the Semana range) starts days before today's open: a whitespace point at today's open would sit BEFORE
  // older candles and break the strictly ascending order setData() requires. The series already begins at or before the anchor, so
  // it needs none.
  const anchorTime = sessionOpenSeconds - 1;
  if (candles[0].time <= anchorTime) return candles.map(chartCandle);
  const anchor = { time: anchorTime as UTCTimestamp };
  return [anchor, ...candles.map(chartCandle)];
}

type CandleSeriesData = ReturnType<typeof withSessionOpenAnchor>;

/**
 * series.setData() plus a guard for a lightweight-charts 5.2.0 quirk, reproduced live on the dashboard 2026-10-18: the FIRST setData() after the chart
 * mounted (the gamma/ATR effect, ~30 s after load when the first poll lands) leaves the time scale's visible logical range null. The series still holds its
 * bars, but priceToCoordinate() returns null and the canvas paints nothing until another setData() runs (the next poll, ~30 s later): "the chart disappears for
 * ~10 s after loading". Feeding the very same data a second time rebuilds the range (measured: single call -> null, second call -> {0..390}), so when the range
 * came back null the data is set once more. Nothing to repair on an empty series (no range is expected then).
 */
export function setSeriesData(chart: IChartApi | null, series: ISeriesApi<"Candlestick">, data: CandleSeriesData): void {
  if (!guardedSetData(series, data, "candles")) return;
  if (!chart || data.length === 0) return;
  if (chart.timeScale().getVisibleLogicalRange() === null) {
    recordChartEvent("range-null-resync", { bars: data.length });
    guardedSetData(series, data, "candles-resync");
  }
}

/**
 * series.setData() that can never take the page down. 2026-10-28 09:39 and 09:41 (Lester's browser, crash log convexa.crashes.v1): 30 s after a symbol switch
 * with EMAs on, the first poll's re-feed of the candles made lightweight-charts throw "Value is null" inside its line renderer, synchronously, inside a React
 * effect, so the error boundary replaced the whole dashboard. The library's internal state is rebuilt by the next setData (every tick or poll), so a failed
 * call is recorded in the chart lifecycle log and skipped instead of propagating. Returns whether the data was set.
 */
export function guardedSetData<T>(series: { setData: (data: T[]) => void }, data: T[], where: string): boolean {
  try {
    series.setData(data);
    return true;
  } catch (error) {
    recordChartEvent("series-error", { where, message: String((error as Error)?.message ?? error).slice(0, 120) });
    return false;
  }
}

type TimePoint = { time: UTCTimestamp; value: number };

// lightweight-charts requires setData() input strictly ascending by time.
// `points` must already be sorted ascending; collapses consecutive points
// that land on the same second (keeping the latest value for that second)
// as a defensive backstop alongside the source-level dedup in dashboard.tsx.
function dedupeAscendingByTime(points: TimePoint[]): TimePoint[] {
  const deduped: TimePoint[] = [];
  for (const point of points) {
    const last = deduped.at(-1);
    if (last && last.time === point.time) {
      deduped[deduped.length - 1] = point;
    } else {
      deduped.push(point);
    }
  }
  return deduped;
}

type AtrLevelValues = { upper: number; lower: number };

// The *inner* band (today_open +/- half of the 14-day ATR), not outer
// (+/- the full ATR) -- confirmed live, 2026-09-23: outer_upper_band -
// outer_lower_band alone spans a full 2x ATR (e.g. ~$136 for SPX, ~$14
// for AAPL on the session checked), roughly double each symbol's own
// real typical daily high-low range (the ATR value itself), which is
// what made the earlier outer-based levels look oversized against every
// symbol checked. inner_upper_band - inner_lower_band spans exactly 1x
// ATR -- the actual average daily true range -- which is the number
// this level is meant to represent.
function atrLevels(atrRange: AtrRange | undefined): AtrLevelValues | null {
  if (!atrRange || atrRange.bands_provisional) return null;
  const { inner_upper_band, inner_lower_band } = atrRange;
  if (inner_upper_band === null || inner_lower_band === null) return null;
  return { upper: inner_upper_band, lower: inner_lower_band };
}

// Fixed pixel thickness, not price-derived -- these represent a single
// level (a specific price), not a range with real height of its own, so
// unlike bandRect below, the rectangle's height is purely a visual
// affordance to make a 1px line legible as a filled bar.
const ATR_LEVEL_THICKNESS_PX = 4;

function levelRect(
  series: ISeriesApi<"Candlestick">,
  price: number,
  thicknessPx: number = ATR_LEVEL_THICKNESS_PX,
): BandRect | null {
  const center = series.priceToCoordinate(price);
  if (center === null) return null;
  return { top: center - thicknessPx / 2, height: thicknessPx };
}

// With zero price variance across the visible candles (e.g. the single
// flat O=H=L=C candle that exists right after mount, before a second live
// poll lands — dashboard-spec.md section 2.2), Lightweight Charts'
// vertical auto-scale collapses to a near-zero range. `priceToCoordinate`
// then maps the ATR band's real price bounds (a few dollars away) to
// coordinates thousands of pixels outside the container, so the resulting
// band `<div>` balloons to dozens of times the chart's actual height —
// invisible before PR #57 only because it rendered behind the chart's own
// canvas layers (z-index 1 vs ~2), not because it was ever correctly
// sized. Skip the bands entirely until there's real range for the price
// scale to anchor to.
function hasPriceRange(candles: MinuteCandle[]): boolean {
  let min = Infinity;
  let max = -Infinity;
  for (const candle of candles) {
    if (candle.low < min) min = candle.low;
    if (candle.high > max) max = candle.high;
  }
  return max > min;
}

// Lightweight Charts' default vertical auto-scale is computed purely from
// the candlestick series' own visible data (its documented behavior) — it
// never expands to include `createPriceLine()` levels or these hand-drawn
// ATR band overlays, since neither is "series data" to the library. With
// only a few (or one, flat) candles, Call Wall/Put Wall/Gamma Flip/ATR
// bounds can sit well outside that tight range and simply never get
// painted — not hidden, just off-canvas. Showing exactly how far price is
// from these reference levels is the whole point of the chart, so the
// visible range must always stretch to include them (VWAP Anclado is a
// real `LineSeries` with real data on the same price scale, so the
// library already folds it into auto-scale on its own — verified live,
// not assumed).
function referenceLevelPrices(
  gamma: GammaResponse,
  atrRange: AtrRange | undefined,
  showAtr: boolean,
  expectedMove: ExpectedMove | undefined,
  showExpectedMove: boolean,
): number[] {
  const levels = showAtr ? atrLevels(atrRange) : null;
  const moveLevels = showExpectedMove ? expectedMoveLevels(expectedMove) : [];
  return [
    ...gammaLevels(gamma)
      .map((level) => level.price)
      .filter((price): price is number => price !== null),
    ...(levels ? [levels.upper, levels.lower] : []),
    ...moveLevels.map((level) => level.price).filter((price): price is number => price !== null),
  ];
}

function mergePriceRange(base: AutoscaleInfo | null, extraPrices: number[]): AutoscaleInfo | null {
  if (extraPrices.length === 0) return base;
  const values = [...extraPrices];
  if (base?.priceRange) {
    values.push(base.priceRange.minValue, base.priceRange.maxValue);
  }
  return {
    priceRange: { minValue: Math.min(...values), maxValue: Math.max(...values) },
    margins: base?.margins,
  };
}

// The metric tabs of the heatmap, in the order of the approved mockup (Gamma | Delta) plus absolute gamma, vanna and charm.
const HEATMAP_METRIC_TABS = [
  { metric: "gamma", labelKey: "heatmapMetricGamma" },
  { metric: "absolute_gamma", labelKey: "heatmapMetricAbsolute" },
  { metric: "total_gamma", labelKey: "heatmapMetricTotal" },
  { metric: "delta", labelKey: "heatmapMetricDelta" },
  { metric: "vanna", labelKey: "heatmapMetricVanna" },
  { metric: "charm", labelKey: "heatmapMetricCharm" },
] as const satisfies readonly { metric: GammaHeatmapMetric; labelKey: string }[];

export function PriceChart({
  symbol,
  candles,
  gamma,
  gammaView = "structural",
  vwapPoints = [],
  vwapNotApplicable = false,
  vwapProxySymbol = null,
  atrRange,
  expectedMove,
  timeframe = "1m",
  candleCountdown = true,
  streamStatus = "connected",
}: PriceChartProps) {
  const { t } = useLanguage();
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const trendlineSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const dragStartRef = useRef<TrendlinePoint | null>(null);
  const initialCandlesRef = useRef(candles);
  const recomputeBandRectsRef = useRef<() => void>(() => {});
  // Guards the synthetic width wiggle in handleSizeChange below against
  // re-triggering itself -- see that function's own comment.
  const forcingRepaintRef = useRef(false);
  // Tracks candle count across renders so the [candles] effect below can
  // tell "the async session-history seed just landed" (a large jump)
  // apart from "one more live tick/poll appended a candle" (a jump of
  // at most 1) -- see that effect's own comment for why this matters.
  const previousCandleCountRef = useRef(candles.length);
  // Real mount time, not a one-shot boolean -- see CANDLE_SEED_SETTLING_
  // WINDOW_MS's own comment for why a bulk jump can legitimately need to
  // re-fit more than once during a slow/retried initial load. Set inside
  // the mount effect below, not here -- an impure Date.now() call during
  // render itself isn't idempotent (and could run more than once per real
  // mount under React Strict Mode's double-invoke), where the effect is
  // guaranteed to run exactly once per real mount, before the [candles]
  // effect (declared after it) ever reads this on that same commit.
  const mountedAtRef = useRef(0);
  // A *second*, independent one-shot re-fit trigger alongside the one
  // above -- see the mount effect's handleSizeChange for why a candle-
  // count jump alone isn't enough to catch every case.
  const hasRefitAfterRealSizeRef = useRef(false);
  // The chart keeps itself fitted to the day's candles until the user touches it (wheel, drag, touch): after that their own pan/zoom is never fought. This replaces "only within 15 s of the mount"
  // for the seed and the size changes (2026-10-21, Lester: after a symbol switch the chart came out spread out and had to be fitted by hand when the history or the layout settled late).
  const userAdjustedRef = useRef(false);
  const lastFitWidthRef = useRef(0);
  const firstCandleTimeRef = useRef<number | null>(candles[0]?.time ?? null);
  // Computed once per mount (the whole component remounts on symbol
  // change via its `key`, so a new session's open is picked up then) --
  // see withSessionOpenAnchor for why this only anchors the left edge.
  // mostRecentSessionRange, not regularSessionRange(Date.now()) directly
  // -- confirmed live, 2026-09 (a real Saturday): on a non-trading day,
  // the plain "today" range is a session that never happened, so any
  // real stale price already on file (from the actual most recent
  // trading day, e.g. Friday) landed *before* that bogus anchor and
  // crashed lightweight-charts' strict ascending-order check. Anchoring
  // against the most recent real session (mostRecentSessionRange's own
  // comment) fixes this at the source, for every symbol, not just the
  // one that happened to be open when this was caught.
  const sessionOpenSecondsRef = useRef(mostRecentSessionRange(Date.now()).openSeconds);
  const [levelMode, setLevelMode] = useState<LevelMode>("static");
  const [history, setHistory] = useState<GammaHistoryItem[]>([]);
  const [showVwap, setShowVwap] = useState(true);
  const [showAtr, setShowAtr] = useState(true);
  const [showExpectedMove, setShowExpectedMove] = useState(true);
  // The line of time and the live profile of the approved mockup (design 9 and 10): history on the left of AHORA, one live profile (a bar per strike) on its right,
  // aligned with the price axis. On by default; it replaces the old bottom GEX-by-strike view. The Smile dots ride on the same line.
  const [showProfile, setShowProfile] = useState(true);
  const [showSmile, setShowSmile] = useState(true);
  // The minute flow panes (calls and puts, under the candles, hidden by default): one histogram series each, in its own pane so the time axis and the crosshair are the candles'.
  const [showFlow, setShowFlow] = useState(false);
  // The flow since the open, summarised from the poll the flow panes already make (no extra request) and shown in a line above the chart; the alerts half comes from the cubes' poll.
  const [openingFlow, setOpeningFlow] = useState<OpeningFlow | null>(null);
  const [openingAlerts, setOpeningAlerts] = useState<OpeningAlerts | null>(null);
  // Moving averages (any period, own colour; chosen in the EMAs menu, kept in this browser). `emaSeed` = closes of the sessions BEFORE the first candle on screen, only used to warm the lines up.
  const [emas, setEmas] = useState<EmaConfig[]>([]);
  const [emasLoaded, setEmasLoaded] = useState(false);
  const [emaSeed, setEmaSeed] = useState<{ key: string; closes: { time: number; close: number }[] }>({ key: "", closes: [] });
  const [convergingEmas, setConvergingEmas] = useState<ReadonlySet<string>>(new Set());
  const emaSeriesRef = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  // The Smile's expiration: "" = the nearest current one; the list of the symbol's current expirations; and the dot under the mouse (strike, IV and where it is on screen).
  // Both are remembered together with the symbol they belong to, so a symbol switch needs no reset: what was chosen for another symbol reads as "nothing chosen".
  const [smileChoice, setSmileChoice] = useState<{ symbol: string; date: string }>({ symbol, date: "" });
  const [smileList, setSmileList] = useState<{ symbol: string; dates: string[] }>({ symbol, dates: [] });
  const smileExpiration = smileChoice.symbol === symbol ? smileChoice.date : "";
  const smileExpirations = smileList.symbol === symbol ? smileList.dates : [];
  const [hoveredSmile, setHoveredSmile] = useState<{ point: SmilePoint; x: number; y: number } | null>(null);
  const profilePrimitiveRef = useRef<LiveProfilePrimitive | null>(null);
  // Figures the profile endpoint does not serve (delta, vanna, charm) come from the latest heatmap values.
  const [heatmapProfile, setHeatmapProfile] = useState<ProfileBar[] | null>(null);
  // The width the chart currently keeps free at the right of the time scale for the profile column (0 = none), and whether the column is on.
  const reservedRightPxRef = useRef(0);
  const profileColumnOnRef = useRef(true);
  const lastCloseRef = useRef<number | null>(candles.at(-1)?.close ?? null);
  const lastCandleTimeRef = useRef<number | null>(candles.at(-1)?.time ?? null);
  // Whale alerts as small squares on the chart (design 12.8 to 12.14); off by default until it is checked against live alerts.
  const [showAlertCubes, setShowAlertCubes] = useState(false);
  // Minimum cube size per symbol (item 12b): the API's suggestion (about 40 cubes a session each, recomputed daily) unless the viewer typed their own, which this browser remembers per symbol.
  const [customCubeMins, setCustomCubeMins] = useState<Record<string, number>>({});
  const [cubeMinsLoaded, setCubeMinsLoaded] = useState(false);
  const [suggestedCubeMins, setSuggestedCubeMins] = useState<{ gross: Record<string, number>; net: Record<string, number> }>({ gross: {}, net: {} });
  const [cubeMinDraft, setCubeMinDraft] = useState<{ symbol: string; text: string } | null>(null);
  // Net-sized cubes (default, Lester 2026-10-23): only the one-sided part of an alert sizes and filters it; "Neto" off goes back to gross. Remembered by this browser.
  const [cubeSizing, setCubeSizing] = useState<CubeSizing>(DEFAULT_CUBE_SIZING);
  const [cubeSizingLoaded, setCubeSizingLoaded] = useState(false);
  const cubeMin = effectiveCubeMin(symbol, customCubeMins, cubeSizing === "net" ? suggestedCubeMins.net : suggestedCubeMins.gross);
  const cubeMinDeq = cubeMin.value;
  const [cubeShowFar, setCubeShowFar] = useState(DEFAULT_CUBE_FILTER.showFar);
  const [cubeAlerts, setCubeAlerts] = useState<WhaleAlert[]>([]);
  const [hoveredCube, setHoveredCube] = useState<{ id: string; x: number; y: number } | null>(null);
  const cubesPrimitiveRef = useRef<AlertCubesPrimitive | null>(null);
  const cubes = useMemo(
    () => buildCubes(cubeAlerts, { minDeq: cubeMinDeq, showFar: cubeShowFar, sizing: cubeSizing }),
    [cubeAlerts, cubeMinDeq, cubeShowFar, cubeSizing],
  );
  // Gamma heatmap behind the candles (off by default): strike x time matrix read from /gamma/{symbol}/heatmap.
  const [showHeatmap, setShowHeatmap] = useState(false);
  // Each minute coloured against its own distribution (history stays as painted), or one scale for the whole session (the chip "Escala del día").
  const [dayScale, setDayScale] = useState(false);
  const [heatmapMetric, setHeatmapMetric] = useState<GammaHeatmapMetric>("gamma");
  // The sides view: call gamma and put gamma drawn apart (green / red, one scale), each switchable. Any side on replaces the single figure above until both are off again.
  const [sideLayers, setSideLayers] = useState({ calls: false, puts: false });
  const sidesOn = sideLayers.calls || sideLayers.puts;
  const [drawMode, setDrawMode] = useState(false);
  const [trendline, setTrendline] = useState<Trendline | null>(null);
  const referenceLevelsRef = useRef(
    referenceLevelPrices(gamma, atrRange, showAtr, expectedMove, showExpectedMove),
  );
  const [atrRects, setAtrRects] = useState<{ upper: BandRect | null; lower: BandRect | null }>({
    upper: null,
    lower: null,
  });

  // The chart is remounted (key) only by a symbol/timeframe change; a mount with neither having changed is exactly what the
  // 2026-10-16 16:00 ET blank chart needs explaining, so every mount/unmount is recorded locally (lib/chart-lifecycle.ts).
  useEffect(() => {
    recordChartEvent("chart-mount", { symbol, timeframe });
    return () => recordChartEvent("chart-unmount", { symbol, timeframe });
  }, [symbol, timeframe]);

  useEffect(() => {
    recordChartEvent("stream-status", { status: streamStatus });
  }, [streamStatus]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    mountedAtRef.current = Date.now();
    userAdjustedRef.current = false;
    const markUserAdjusted = () => {
      userAdjustedRef.current = true;
    };
    container.addEventListener("wheel", markUserAdjusted, { passive: true });
    container.addEventListener("pointerdown", markUserAdjusted, { passive: true });

    const chart = createChart(container, {
      // `autoSize` delegates to the library's own internal ResizeObserver,
      // which correctly resizes the canvas backing buffer as the flex
      // layout settles. A hand-rolled `ResizeObserver` + `applyOptions`
      // here previously left the canvas's actual pixel buffer stuck at the
      // browser's 300x150 default whenever the container's height came
      // from a flex chain instead of a fixed CSS height (as it does now,
      // with PriceChart as the dominant center column) — every price
      // coordinate `priceToCoordinate()` computed (what the ATR bands
      // overlay uses) was then scaled against that tiny buffer instead of
      // the CSS-stretched display size, drifting away from where the
      // candles actually render. `width`/`height` below are only the
      // documented fallback for when ResizeObserver is unavailable.
      autoSize: true,
      width: container.clientWidth,
      height: container.clientHeight || 420,
      layout: {
        // The same face as the page (next/font's Inter), read from the body so the axis labels match the rest of the UI.
        fontFamily: typeof document === "undefined" ? undefined : getComputedStyle(document.body).fontFamily || undefined,
        // Per user request (2026-09-23): a near-black chart body, distinct
        // from the lighter navy --tv-panel background the rest of the
        // dashboard's panels use -- solid rather than "transparent" so
        // this stays the chart's own color regardless of what sits behind
        // it in .price-chart-frame.
        background: { type: ColorType.Solid, color: "#050608" },
        textColor: "#787b86",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "rgba(42, 46, 57, 0.6)" },
        horzLines: { color: "rgba(42, 46, 57, 0.6)" },
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter,
        // The candles stop at the AHORA line: the live profile column owns the strip to its right (live-profile-primitive.ts).
        rightOffsetPixels: reservedRightPxRef.current = profileColumnOnRef.current ? profileWidthCssPx(container.clientWidth) : 0,
      },
      rightPriceScale: { borderColor: "#2A2E39" },
      localization: { timeFormatter: crosshairTimeFormatter },
      // Per user request (2026-09-26): the default (Magnet) snaps the
      // crosshair's horizontal line to each candle's own OHLC value
      // instead of following the mouse's actual Y position -- exactly
      // what makes it impossible to point at a real reversal that
      // happened mid-wick, not at one of those four fixed prices.
      // Normal lets it move freely, matching what a trader actually
      // wants: read off whatever price level the mouse is really over.
      crosshair: { mode: CrosshairMode.Normal },
    });
    // TradingView-native candle colors — never the Convexa brand pair above,
    // which is reserved for what Convexa itself calculates (Gamma levels).
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#26A69A",
      downColor: "#EF5350",
      borderVisible: false,
      wickUpColor: "#26A69A",
      wickDownColor: "#EF5350",
      autoscaleInfoProvider: (original: () => AutoscaleInfo | null) =>
        mergePriceRange(original(), referenceLevelsRef.current),
    });
    series.setData(withSessionOpenAnchor(initialCandlesRef.current, sessionOpenSecondsRef.current));
    fitCandles(chart, () => chartRef.current === chart && !userAdjustedRef.current, () => reservedRightPxRef.current);
    chartRef.current = chart;
    seriesRef.current = series;

    // Created once, alongside the candlestick series, and kept for the
    // whole component lifetime — its data is just re-set (see the
    // `[trendline]` effect below) whenever the user draws or clears the
    // line, rather than recreating the series itself each time.
    const trendlineSeries = chart.addSeries(LineSeries, {
      color: TRENDLINE_COLOR,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    trendlineSeriesRef.current = trendlineSeries;

    const handleSizeChange = (width: number) => {
      // The wiggle below fires this same callback again (it resizes the
      // very container this is subscribed to) -- ignore those synthetic
      // re-entries so they don't recurse into another wiggle, or re-run
      // the one-shot re-fit/recompute logic for a resize this component
      // caused itself rather than a real one.
      if (forcingRepaintRef.current) return;
      recordChartEvent("chart-size", { width });
      // The profile column is a fifth of the pane: its reserved strip follows the pane's width. Before the one-shot fit below, so that fit already counts it.
      const reserved = profileColumnOnRef.current ? profileWidthCssPx(width) : 0;
      if (reserved !== reservedRightPxRef.current) {
        reservedRightPxRef.current = reserved;
        chart.applyOptions({ timeScale: { rightOffsetPixels: reserved } });
      }
      recomputeBandRectsRef.current();
      // `autoSize`'s internal ResizeObserver (see the option's own comment
      // above) can report the container's real pixel width well after
      // this effect's own synchronous fitContent() call already ran
      // against a zero/fallback width -- confirmed live, 2026-09-17: for
      // fast-loading symbols (AAPL, SPY) the full day's candles are
      // already present at first render, so the jump-detection re-fit in
      // the [candles] effect below never fires (there's no "empty then
      // jump" transition to catch) and that first, too-early fitContent()
      // call was the only one ever made -- silently producing a null
      // logical range and abnormally wide candles. Re-fit exactly once,
      // the first time a real (nonzero) width is actually reported,
      // regardless of whether the jump-based re-fit ever fires too; a
      // later, unrelated resize (window resize, sidebar toggle) must not
      // keep re-fitting and fighting the user's own pan/zoom.
      if (width > 0 && (!hasRefitAfterRealSizeRef.current || (!userAdjustedRef.current && width !== lastFitWidthRef.current))) {
        hasRefitAfterRealSizeRef.current = true;
        lastFitWidthRef.current = width;
        fitCandles(chart, () => chartRef.current === chart && !userAdjustedRef.current, () => reservedRightPxRef.current);
      }
      // Works around a real, confirmed lightweight-charts (5.2.0) resize bug
      // -- live, 2026-09-23: after some resizes, the candlestick series and
      // both axis-label canvas layers stop painting entirely (sampled their
      // pixels directly via canvas.getContext('2d').getImageData() during
      // the blank state: fully transparent, [0,0,0,0], even though the
      // canvas elements themselves are correctly sized) while the
      // background layer keeps rendering fine. A full series.setData() call
      // with the exact same data does NOT recover it; only destroying and
      // recreating the whole chart does (confirmed by switching timeframe,
      // which remounts PriceChart via its own React `key`) -- so this is a
      // stuck internal paint/invalidation state in the existing chart
      // instance, not a data problem this component's own state can fix by
      // re-feeding it data.
      //
      // IChartApiBase.resize(width, height, forceRepaint) looked like the
      // library's own sanctioned way to force this -- it isn't, with
      // autoSize on: confirmed by reading the library's own bundled source,
      // the public resize() method's very first check is `if
      // (this.autoSizeActive()) { warn(...); return; }` -- a hard, silent
      // no-op before it ever reaches the internal repaint logic (the
      // console warning it prints was the giveaway once looked for). The
      // library's OWN internal ResizeObserver callback (autoSize's own
      // implementation) does call `_internal_resize(w, h, true)` directly,
      // bypassing that guard -- so the only way to reach the same working
      // code path from outside is to make the browser fire a real
      // ResizeObserver entry on the observed container, the same as an
      // actual window/layout resize would. Nudging the container's own
      // width by 1px and immediately back does exactly that -- twice, both
      // through the library's real (autoSize-internal) forceRepaint path,
      // not the guarded public one.
      const container = containerRef.current;
      if (container && width > 1) {
        forcingRepaintRef.current = true;
        const originalWidth = container.style.width;
        container.style.width = `${width - 1}px`;
        // setTimeout, not requestAnimationFrame -- rAF can be suspended
        // indefinitely on a backgrounded/unfocused tab (confirmed live:
        // an rAF-chained version of this same restore never ran at all in
        // an automated, not-frontmost browser tab, permanently stuck with
        // forcingRepaintRef left true and every future resize silently
        // ignored). A timer fires on wall-clock time regardless of paint
        // scheduling, so the guard can't get stuck the same way.
        window.setTimeout(() => {
          if (containerRef.current === container) {
            container.style.width = originalWidth;
          }
          // A second, later timer, not the same tick -- the restore above
          // is itself a resize this same guard must still swallow before
          // it's safe to let a genuinely new resize back in.
          window.setTimeout(() => {
            forcingRepaintRef.current = false;
          }, 50);
        }, 0);
      }
    };
    chart.timeScale().subscribeSizeChange(handleSizeChange);

    return () => {
      chart.timeScale().unsubscribeSizeChange(handleSizeChange);
      container.removeEventListener("wheel", markUserAdjusted);
      container.removeEventListener("pointerdown", markUserAdjusted);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      trendlineSeriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    const previousCandleCount = previousCandleCountRef.current;
    previousCandleCountRef.current = candles.length;
    lastCloseRef.current = candles.at(-1)?.close ?? null;
    lastCandleTimeRef.current = candles.at(-1)?.time ?? null;
    lastOpenRef.current = candles.at(-1)?.open ?? null;
    // Bulk changes only (a seed landing, a reset to zero): single live ticks would flood the 80-entry log.
    if (previousCandleCount === 0 || Math.abs(candles.length - previousCandleCount) > 1) {
      recordChartEvent("candles", { count: candles.length, previous: previousCandleCount });
    }
    const latest = candles.at(-1);
    const series = seriesRef.current;
    if (!latest || !series) return;
    // A bulk jump of more than one candle (the day's history seeded over a few dots, e.g. ES/NQ right after the opening price is
    // saved: confirmed 2026-10-12, only dots until F5) must also resync with setData(): update() appends ONE bar, so
    // the seeded candles before the last one never reached the series.
    const bulkJump = candles.length - previousCandleCount > 1;
    if (previousCandleCount === 0 || bulkJump) {
      // The series currently holds *zero* bars, not even the whitespace
      // anchor -- either the mount effect's own setData(withSessionOpenAnchor
      // ([], ...)) ran with no real candle yet (right after a symbol
      // switch), or this is candles' very first non-empty render.
      // series.update() assumes an already-initialized series to append a
      // single bar onto; calling it on a genuinely empty one doesn't throw
      // synchronously, but leaves lightweight-charts' internal pane-view
      // bar count out of sync with its own backing array -- confirmed live,
      // 2026-09-22: a real, reproducible *uncaught* "Value is null" crash
      // fires on a later, unrelated render, deep inside the library's own
      // bar-styling code, immune to the try/catch below since it isn't
      // thrown by this update() call itself. setData() fully re-establishes
      // internal state from scratch and has no such assumption, so every
      // zero-to-nonzero transition must go through it, not update().
      setSeriesData(chartRef.current, series, withSessionOpenAnchor(candles, sessionOpenSecondsRef.current));
    } else {
      try {
        series.update(chartCandle(latest));
      } catch (error) {
        // lightweight-charts' *only* throw on this call path is "Cannot
        // update oldest data" -- update() requires strictly non-decreasing
        // time versus whatever the series already has, unlike setData()
        // below, which has no such requirement and fully re-establishes
        // truth from `candles` (same full-resync primitive the
        // gamma/atrRange/showAtr effect further down already relies on).
        // `candles` itself is always freshly rebuilt ascending from the
        // full accumulated pricePoints (see aggregateMinuteCandles/
        // aggregateCandles in lib/candles.ts), so this should be
        // unreachable in steady state -- confirmed live, 2026-09, no
        // ordering violation across a real market open. Kept as a recovery
        // path rather than a crash for whatever this component can't
        // control end to end: a stale `seriesRef` surviving a dev-only
        // Fast Refresh reload being the concrete case found, not ruled out
        // for good going forward.
        console.warn("PriceChart: series.update() rejected the latest candle, resyncing via setData()", error);
        setSeriesData(chartRef.current, series, withSessionOpenAnchor(candles, sessionOpenSecondsRef.current));
      }
    }
    // The mount effect's fitContent() call above only ever saw whatever
    // sliver of `candles` existed synchronously at first render --
    // dashboard.tsx seeds the real session history asynchronously right
    // after mount (seedThenPoll), so that call fit to near-nothing and
    // was never re-run once the real history landed, leaving every
    // remount showing abnormally wide candles until the user manually
    // adjusted the visible range (confirmed live, 2026-09-17). A jump of
    // more than one candle at once means a bulk seed just landed, not a
    // single live tick/poll appending its own new bar -- re-fit for that
    // transition, not on every subsequent live update.
    // This is a *separate* trigger from handleSizeChange's own re-fit
    // above -- a symbol whose full history is already present at first
    // render never has a jump for this to catch (see that handler's
    // comment), so both must exist independently to cover every case.
    //
    // Bounded by CANDLE_SEED_SETTLING_WINDOW_MS, not a one-shot boolean --
    // confirmed live, 2026-09-21: under a slow/retried load, more than one
    // qualifying jump can land in the first several seconds (a partial
    // response superseded by the real one), and the original "exactly
    // once, ever" guard could spend its single re-fit on an early, still-
    // partial jump, permanently under-fitting the chart even once the
    // rest of the day's candles arrived moments later.
    // Also while the user has not touched the chart (not only in the first 15 s): a slow history load, or a late layout, no longer leaves it spread out. A different FIRST candle means another
    // history landed (a symbol or timeframe swap that kept the count), which is a new picture to fit too.
    const firstCandleTime = candles[0]?.time ?? null;
    const historyChanged = firstCandleTimeRef.current !== null && firstCandleTime !== null && firstCandleTime !== firstCandleTimeRef.current;
    firstCandleTimeRef.current = firstCandleTime;
    if (
      (candles.length - previousCandleCount > 1 &&
        (Date.now() - mountedAtRef.current < CANDLE_SEED_SETTLING_WINDOW_MS || !userAdjustedRef.current)) ||
      (historyChanged && !userAdjustedRef.current)
    ) {
      if (chartRef.current) {
        const chart = chartRef.current;
        fitCandles(chart, () => chartRef.current === chart && !userAdjustedRef.current, () => reservedRightPxRef.current);
      }
    }
  }, [candles]);

  // Live profile: a series primitive with the AHORA line and one bar per strike in the strip the chart keeps free at the right of the time scale. Attached only while the
  // toggle is on; the strip itself is reserved below (rightOffsetPixels) and follows the toggle.
  useEffect(() => {
    const series = seriesRef.current;
    if (!showProfile || !series) return;
    const primitive = new LiveProfilePrimitive();
    profilePrimitiveRef.current = primitive;
    series.attachPrimitive(primitive);
    return () => {
      if (profilePrimitiveRef.current === primitive) profilePrimitiveRef.current = null;
      // On unmount the chart is already removed: detaching would ask it for a redraw, and that frame then fails with "Object is disposed".
      if (seriesRef.current === series) series.detachPrimitive(primitive);
    };
  }, [showProfile]);

  // The strip: reserved while the profile is on, released when it is off (the chart then keeps its old full-width look).
  useEffect(() => {
    profileColumnOnRef.current = showProfile;
    const chart = chartRef.current;
    const container = containerRef.current;
    if (!chart || !container) return;
    const reserved = showProfile ? profileWidthCssPx(container.clientWidth) : 0;
    if (reserved === reservedRightPxRef.current) return;
    reservedRightPxRef.current = reserved;
    chart.applyOptions({ timeScale: { rightOffsetPixels: reserved } });
    fitCandles(chart, () => chartRef.current === chart, () => reservedRightPxRef.current);
  }, [showProfile]);

  // What the profile shows follows the heatmap's figure while the heatmap is on (net gamma otherwise).
  const profileMetric: GammaHeatmapMetric = showHeatmap && !sidesOn ? heatmapMetric : "gamma";
  const profileFromEndpoint = !(showHeatmap && sidesOn) && (profileMetric === "gamma" || profileMetric === "absolute_gamma");
  const profileCaption = showHeatmap && sidesOn
    ? sideLayers.calls && sideLayers.puts
      ? t.priceChart.profileCaptionSides.both
      : sideLayers.calls
        ? t.priceChart.profileCaptionSides.calls
        : t.priceChart.profileCaptionSides.puts
    : t.priceChart.profileCaptions[profileMetric];
  useEffect(() => {
    if (!showProfile || !profileFromEndpoint) return;
    const controller = new AbortController();
    const refresh = () => {
      getGammaProfile(symbol, gammaView, controller.signal)
        .then((profile) => {
          const absolute = profileMetric === "absolute_gamma";
          profilePrimitiveRef.current?.setBars(
            profile.items.map((item) => ({
              strike: item.strike,
              net: absolute ? item.absolute_gamma : item.net_gamma,
              absolute: item.absolute_gamma,
            })),
            paletteForMetric(profileMetric),
            t.priceChart.profileCaptions[profileMetric],
          );
        })
        .catch(() => {
          // A failed poll keeps the bars already drawn; the next one retries.
        });
    };
    refresh();
    const interval = window.setInterval(refresh, LIVE_LEVELS_POLLING_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [showProfile, symbol, gammaView, profileMetric, profileFromEndpoint, t]);

  useEffect(() => {
    if (!showProfile || profileFromEndpoint || !heatmapProfile) return;
    profilePrimitiveRef.current?.setBars(heatmapProfile, showHeatmap && sidesOn ? "gamma" : paletteForMetric(profileMetric), profileCaption);
  }, [showProfile, profileFromEndpoint, heatmapProfile, profileMetric, profileCaption, showHeatmap, sidesOn]);

  // The viewer's own cube minimums load once after mount (never during render) and save on every change after that; the suggestions are asked whenever the cubes are switched on (the API caches them for an hour).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads this browser's saved minimums once after mount (localStorage does not exist on the server, so it cannot be the initial state)
    setCustomCubeMins(loadCubeMins());
    setCubeMinsLoaded(true);
    setCubeSizing(loadCubeSizing());
    setCubeSizingLoaded(true);
  }, []);
  useEffect(() => {
    if (cubeMinsLoaded) saveCubeMins(customCubeMins);
  }, [customCubeMins, cubeMinsLoaded]);
  useEffect(() => {
    if (cubeSizingLoaded) saveCubeSizing(cubeSizing);
  }, [cubeSizing, cubeSizingLoaded]);
  useEffect(() => {
    if (!showAlertCubes) return;
    const controller = new AbortController();
    getCubeMinimums(controller.signal)
      // A symbol without a net suggestion (or an API older than it) falls back to its gross one, never to the old shared 200.
      .then((response) => setSuggestedCubeMins({ gross: response.minimums, net: { ...response.minimums, ...(response.net_minimums ?? {}) } }))
      .catch(() => {
        // No suggestion: every symbol keeps the legacy default until the next try.
      });
    return () => controller.abort();
  }, [showAlertCubes]);
  const setCubeMinFor = (value: number | null) => {
    const key = symbol.toUpperCase();
    setCustomCubeMins((previous) => {
      const next = { ...previous };
      if (value === null) delete next[key];
      else next[key] = value;
      return next;
    });
    setCubeMinDraft(null);
  };
  const commitCubeMinDraft = () => {
    const parsed = cubeMinDraft && cubeMinDraft.symbol === symbol ? parseCubeMin(cubeMinDraft.text) : null;
    if (parsed === null) setCubeMinDraft(null);
    else setCubeMinFor(parsed);
  };

  // EMAs. The list lives in this browser (localStorage); it loads once after mount (never during render) and saves on every change after that.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads this browser's saved list once after mount (localStorage does not exist on the server, so it cannot be the initial state)
    setEmas(loadEmas());
    setEmasLoaded(true);
  }, []);
  useEffect(() => {
    if (emasLoaded) saveEmas(emas);
  }, [emas, emasLoaded]);

  // Warm-up closes: the candles of the sessions before the first one on screen, same timeframe, asked once per symbol / timeframe while a line is showing (they are history). ES / NQ have
  // no stored candles (404): their lines start at the first candle of the day. A failed request just leaves the lines to settle from the day's own candles.
  const anyEmaVisible = emas.some((ema) => ema.visible);
  useEffect(() => {
    if (!anyEmaVisible) return;
    const key = `${symbol}|${timeframe}`;
    const controller = new AbortController();
    getPriceCandles(symbol, timeframe, timeframe === "1m" ? 5 : 10, controller.signal)
      .then((response) => setEmaSeed({ key, closes: response.candles.map((candle) => ({ time: candle.time, close: candle.close })) }))
      .catch(() => setEmaSeed({ key, closes: [] }));
    return () => controller.abort();
  }, [anyEmaVisible, symbol, timeframe]);

  // One line series per visible EMA (created / removed when the list, a colour, the symbol or the timeframe changes), then its values on every candle update. The line only gets a point
  // at the time of each candle ON SCREEN: points of the warm-up sessions would widen the shared time scale and break fitContent (the VWAP lesson of 2026-09-18).
  const emaStructure = emas
    .filter((ema) => ema.visible)
    .map((ema) => `${ema.id}:${ema.color}`)
    .join("|");
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || emaStructure === "") return;
    const series = new Map<string, ISeriesApi<"Line">>();
    for (const ema of emas) {
      if (!ema.visible) continue;
      series.set(
        ema.id,
        chart.addSeries(LineSeries, { color: ema.color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }),
      );
    }
    emaSeriesRef.current = series;
    return () => {
      emaSeriesRef.current = new Map();
      // On unmount (symbol / timeframe / Día-Semana switch: the chart is keyed) the chart effect above has already called chart.remove(), and
      // removeSeries on a removed chart throws "Value is undefined", which took the whole page down (2026-10-24, Semana with EMAs on).
      if (chartRef.current !== chart) return;
      for (const line of series.values()) chart.removeSeries(line);
    };
    // `emas` only matters through emaStructure (visibility and colour); the closes below read the full list.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [emaStructure, symbol, timeframe]);
  useEffect(() => {
    const series = emaSeriesRef.current;
    if (series.size === 0 || candles.length === 0) return;
    const firstTime = candles[0].time;
    const seed = emaSeed.key === `${symbol}|${timeframe}` ? emaSeed.closes.filter((entry) => entry.time < firstTime).map((entry) => entry.close) : [];
    const closes = candles.map((candle) => candle.close);
    const converging = new Set<string>();
    for (const ema of emas) {
      const line = series.get(ema.id);
      if (!line) continue;
      const { values, warm } = emaValues(seed, closes, ema.period);
      if (!warm) converging.add(ema.id);
      guardedSetData(
        line,
        candles
          .map((candle, index) => ({ time: candle.time as UTCTimestamp, value: values[index] }))
          .filter((point) => Number.isFinite(point.value)),
        `ema-${ema.period}`,
      );
    }
    setConvergingEmas((previous) => (previous.size === converging.size && [...converging].every((id) => previous.has(id)) ? previous : converging));
  }, [candles, emas, emaSeed, emaStructure, symbol, timeframe]);

  // Option flow under the candles: two small panes (calls, puts) sharing the candles' time scale, polled like the other live panels; a failed poll keeps the bars already drawn.
  useEffect(() => {
    const chart = chartRef.current;
    if (!showFlow || !chart) return;
    const options = { priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "price" as const, precision: 0, minMove: 1 } };
    const calls = chart.addSeries(HistogramSeries, { ...options, title: t.priceChart.flowCallsTitle }, 1);
    const puts = chart.addSeries(HistogramSeries, { ...options, title: t.priceChart.flowPutsTitle }, 2);
    chart.panes?.().forEach((pane, index) => {
      if (index > 0) pane.setHeight(64);
    });
    const controller = new AbortController();
    const refresh = () => {
      const start = new Date(heatmapStartSeconds(lastCandleTimeRef.current) * 1000);
      getMinuteFlow(symbol, { start }, controller.signal)
        .then((response) => {
          setOpeningFlow(summarizeOpeningFlow(response, heatmapStartSeconds(lastCandleTimeRef.current) + 3600));
          const bars = flowBarsFromResponse(response, timeframe);
          calls.setData(bars.calls.map((bar) => ({ ...bar, time: bar.time as UTCTimestamp })));
          puts.setData(bars.puts.map((bar) => ({ ...bar, time: bar.time as UTCTimestamp })));
        })
        .catch(() => {
          // A failed poll keeps the bars already drawn; the next one retries.
        });
    };
    refresh();
    const interval = window.setInterval(refresh, LIVE_LEVELS_POLLING_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(interval);
      setOpeningFlow(null);
      // Same as the EMA lines: never touch a chart that unmounting already removed.
      if (chartRef.current !== chart) return;
      chart.removeSeries(calls);
      chart.removeSeries(puts);
    };
  }, [showFlow, symbol, timeframe, t]);

  // Smile dots on the AHORA line: the nearest current expiration, polled like the other live panels; a failed poll keeps the dots already drawn.
  useEffect(() => {
    const primitive = profilePrimitiveRef.current;
    if (!showProfile || !showSmile || !primitive) return;
    const controller = new AbortController();
    let expiration: string | null = smileExpiration || null;
    const load = async () => {
      if (expiration === null || expiration < currentEasternDate()) {
        const response = await getOptionChainExpirations(symbol, controller.signal);
        const current = currentExpirations(response.expirations);
        setSmileList({ symbol, dates: current });
        expiration = smileExpiration && current.includes(smileExpiration) ? smileExpiration : (current[0] ?? null);
      }
      if (expiration === null) {
        primitive.setSmile([]);
        return;
      }
      const chain = await getOptionChain(symbol, expiration, controller.signal);
      const spot = lastCloseRef.current;
      primitive.setSmile(spot !== null && spot > 0 ? smilePointsFromChain(chain.contracts, spot) : []);
    };
    const refresh = () => {
      load().catch(() => {
        // A failed poll keeps the dots already drawn; the next one retries.
      });
    };
    refresh();
    const interval = window.setInterval(refresh, POLLING_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(interval);
      primitive.setSmile([]);
    };
  }, [showProfile, showSmile, symbol, smileExpiration]);

  // Hovering a smile dot: its strike, IV and expiration. The chart reports the pointer through the crosshair event; the primitive finds the dot under it.
  useEffect(() => {
    const chart = chartRef.current;
    const primitive = profilePrimitiveRef.current;
    if (!chart || !primitive || !showProfile || !showSmile) return;
    const onCrosshairMove: Parameters<typeof chart.subscribeCrosshairMove>[0] = (param) => {
      const hit = param.point ? primitive.hitSmile(param.point.x, param.point.y) : null;
      setHoveredSmile(hit && param.point ? { point: hit, x: param.point.x, y: param.point.y } : null);
    };
    chart.subscribeCrosshairMove(onCrosshairMove);
    return () => {
      chart.unsubscribeCrosshairMove(onCrosshairMove);
      setHoveredSmile(null);
    };
  }, [showProfile, showSmile, symbol]);

  // Alert cubes: a series primitive drawn on top of the candles. Attached only while the toggle is on; the alerts of the symbol are polled like
  // the other live panels and a failed poll keeps what is drawn. The chart reports the hovered cube (hitTest) through its crosshair event.
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!showAlertCubes || !series || !chart) return;
    const primitive = new AlertCubesPrimitive();
    cubesPrimitiveRef.current = primitive;
    series.attachPrimitive(primitive);
    const onCrosshairMove: Parameters<typeof chart.subscribeCrosshairMove>[0] = (param) => {
      const id = typeof param.hoveredObjectId === "string" ? param.hoveredObjectId : null;
      primitive.setHovered(id);
      setHoveredCube(id && param.point ? { id, x: param.point.x, y: param.point.y } : null);
    };
    chart.subscribeCrosshairMove(onCrosshairMove);
    const controller = new AbortController();
    const refresh = () => {
      getAlerts(symbol, controller.signal, 500)
        .then((response) => {
          setCubeAlerts(response.alerts);
          setOpeningAlerts(summarizeOpeningAlerts(response.alerts, heatmapStartSeconds(lastCandleTimeRef.current) + 3600));
        })
        .catch(() => {
          // A failed poll keeps the cubes already drawn; the next one retries.
        });
    };
    refresh();
    const interval = window.setInterval(refresh, POLLING_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(interval);
      if (chartRef.current === chart) {
        chart.unsubscribeCrosshairMove(onCrosshairMove);
        series.detachPrimitive(primitive);
      }
      cubesPrimitiveRef.current = null;
      setHoveredCube(null);
      setOpeningAlerts(null);
    };
  }, [showAlertCubes, symbol]);

  useEffect(() => {
    cubesPrimitiveRef.current?.setCubes(cubes);
  }, [cubes, showAlertCubes]);
  // Gamma heatmap: a series primitive drawn behind the candles. The matrix covers the most recent session (from an hour before the open) and is
  // refreshed like the other live panels; a failed poll keeps what is drawn, and a symbol without data (ES/NQ before the opening price
  // is entered, a weekend) simply stays empty.
  useEffect(() => {
    const series = seriesRef.current;
    if (!showHeatmap || sidesOn || !series) return;
    const primitive = new GammaHeatmapPrimitive({ smooth: true });
    series.attachPrimitive(primitive);
    const controller = new AbortController();
    let timer: number | undefined;
    // Self-rescheduling poll: every 10 s while the fast layer feeds the map, every 15 s on the cycle's own one-minute columns.
    const refresh = () => {
      // The session of the candles on screen (Friday's before Monday's open), not simply "today".
      const start = new Date(heatmapStartSeconds(lastCandleTimeRef.current) * 1000);
      let fast = false;
      loadHeatmap(symbol, { view: gammaView, metric: heatmapMetric, start }, controller.signal)
        .then((loaded) => {
          fast = loaded.fast;
          const data = { ...loaded.data, scaleMode: dayScale ? ("day" as const) : ("column" as const) };
          primitive.setData(data);
          setHeatmapProfile(profileBarsFromHeatmap(data));
        })
        .catch(() => {
          // A failed poll keeps the cells already drawn; the next one retries.
        })
        .finally(() => {
          if (!controller.signal.aborted) {
            timer = window.setTimeout(refresh, fast ? HEATMAP_FAST_POLLING_INTERVAL_MS : LIVE_LEVELS_POLLING_INTERVAL_MS);
          }
        });
    };
    refresh();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
      if (seriesRef.current === series) series.detachPrimitive(primitive);
      setHeatmapProfile(null);
    };
  }, [showHeatmap, sidesOn, symbol, gammaView, heatmapMetric, dayScale]);

  // The sides view: ONE heatmap primitive that draws call gamma (green) and put gamma (red) from two requests with the same shared scale (the larger p98); a cell strong on both sides fades to
  // white (sidesRgb), never amber. The live profile shows both as a green and a red bar per strike. A failed poll keeps what is drawn.
  useEffect(() => {
    const series = seriesRef.current;
    if (!showHeatmap || !sidesOn || !series) return;
    const controller = new AbortController();
    type SideLayer = { key: "calls" | "puts"; metric: "call_gamma" | "put_gamma" };
    const layers: SideLayer[] = [];
    if (sideLayers.calls) layers.push({ key: "calls", metric: "call_gamma" });
    if (sideLayers.puts) layers.push({ key: "puts", metric: "put_gamma" });
    const primitive = new GammaHeatmapPrimitive({ smooth: true });
    series.attachPrimitive(primitive);
    const latest: { calls: HeatmapData | null; puts: HeatmapData | null } = { calls: null, puts: null };
    const render = () => {
      primitive.setData(mergeSides(latest.calls, latest.puts));
      setHeatmapProfile(sideBarsFromHeatmaps(latest.calls, latest.puts));
    };
    let timer: number | undefined;
    const refresh = () => {
      const start = new Date(heatmapStartSeconds(lastCandleTimeRef.current) * 1000);
      let fast = false;
      Promise.all(
        layers.map((layer) =>
          loadHeatmap(symbol, { view: gammaView, metric: layer.metric, start }, controller.signal)
            .then((loaded) => {
              fast = fast || loaded.fast;
              latest[layer.key] = { ...loaded.data, scaleMode: dayScale ? ("day" as const) : ("column" as const) };
              render();
            })
            .catch(() => {
              // A failed poll keeps the cells already drawn; the next one retries.
            }),
        ),
      ).finally(() => {
        if (!controller.signal.aborted) {
          timer = window.setTimeout(refresh, fast ? HEATMAP_FAST_POLLING_INTERVAL_MS : LIVE_LEVELS_POLLING_INTERVAL_MS);
        }
      });
    };
    refresh();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
      if (seriesRef.current === series) series.detachPrimitive(primitive);
      setHeatmapProfile(null);
    };
  }, [showHeatmap, sidesOn, sideLayers.calls, sideLayers.puts, symbol, gammaView, dayScale]);

  useEffect(() => {
    trendlineSeriesRef.current?.setData(
      trendline
        ? // Lightweight Charts requires setData() input strictly ascending
          // by time — the user can drag either direction, so the two
          // endpoints aren't necessarily already in that order.
          [trendline.start, trendline.end]
            .sort((left, right) => left.time - right.time)
            .map((point) => ({ time: point.time, value: point.price }))
        : [],
    );
  }, [trendline]);

  // Drawing a line by dragging conflicts with the chart's own default
  // click-drag gesture (panning) — disable pan/zoom for the duration of draw
  // mode instead of trying to make both interpret the same mouse events.
  useEffect(() => {
    chartRef.current?.applyOptions({ handleScroll: !drawMode, handleScale: !drawMode });
  }, [drawMode]);

  useEffect(() => {
    const container = containerRef.current;
    const chart = chartRef.current;
    const series = seriesRef.current;
    if (!drawMode || !container || !chart || !series) return;

    const pointFromEvent = (event: MouseEvent): TrendlinePoint | null => {
      const rect = container.getBoundingClientRect();
      const time = chart.timeScale().coordinateToTime(event.clientX - rect.left);
      const price = series.coordinateToPrice(event.clientY - rect.top);
      if (time === null || price === null) return null;
      return { time: time as UTCTimestamp, price };
    };

    const handleMouseDown = (event: MouseEvent) => {
      dragStartRef.current = pointFromEvent(event);
    };

    const handleMouseMove = (event: MouseEvent) => {
      const start = dragStartRef.current;
      if (!start) return;
      const end = pointFromEvent(event);
      // Two points at the same bar time would violate setData()'s strictly
      // ascending requirement — treat that as "not enough drag yet" rather
      // than drawing a degenerate single-instant line.
      if (!end || end.time === start.time) return;
      setTrendline({ start, end });
    };

    const handleMouseUp = () => {
      dragStartRef.current = null;
    };

    container.addEventListener("mousedown", handleMouseDown);
    container.addEventListener("mousemove", handleMouseMove);
    // On window, not the container: releasing the mouse outside the chart
    // must still end the drag, or the next mouse move over the chart would
    // resume dragging the old line.
    window.addEventListener("mouseup", handleMouseUp);

    return () => {
      container.removeEventListener("mousedown", handleMouseDown);
      container.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
      dragStartRef.current = null;
    };
  }, [drawMode]);

  useEffect(() => {
    if (levelMode !== "historical") return;
    const controller = new AbortController();
    getGammaHistory(symbol, gammaView, controller.signal)
      .then(({ items }) => setHistory(items))
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          console.error("No se pudo cargar el histórico de niveles", reason);
        }
      });
    return () => controller.abort();
  }, [levelMode, symbol, gammaView]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series || levelMode !== "static") return;
    // gamma_flip can legitimately be null (no sign crossing found in the
    // current window) -- createPriceLine() asserts price is a number and
    // throws otherwise, so that level is skipped entirely rather than
    // drawn. Containment only; how "no crossing" should look is a
    // separate, still-pending design decision.
    const lines: IPriceLine[] = gammaLevels(gamma)
      .filter((level): level is GammaLevel & { price: number } => level.price !== null)
      .map((level) =>
        series.createPriceLine({
          ...level,
          lineStyle: LineStyle.Dashed,
          lineWidth: 1,
          axisLabelVisible: true,
        }),
      );
    return () => {
      // The chart-creation effect's cleanup may already have disposed the
      // chart (and this series with it) — e.g. on a symbol change, which
      // unmounts this whole component via its `key`. Calling
      // removePriceLine on an already-disposed series throws.
      if (seriesRef.current !== series) return;
      lines.forEach((line) => series.removePriceLine(line));
    };
  }, [gamma, levelMode]);

  // Independent of levelMode -- unlike Call Wall/Put Wall/Gamma Flip
  // above, EMC/EMP have no "historical" line-series equivalent to switch
  // to, so they stay drawn as price lines regardless of that toggle
  // (same treatment ATR/VWAP already get as plain "Overlays").
  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !showExpectedMove) return;
    const lines: IPriceLine[] = expectedMoveLevels(expectedMove)
      .filter((level): level is GammaLevel & { price: number } => level.price !== null)
      .map((level) =>
        series.createPriceLine({
          ...level,
          lineStyle: LineStyle.Dashed,
          lineWidth: 1,
          axisLabelVisible: true,
        }),
      );
    return () => {
      if (seriesRef.current !== series) return;
      lines.forEach((line) => series.removePriceLine(line));
    };
  }, [expectedMove, showExpectedMove]);

  useEffect(() => {
    referenceLevelsRef.current = referenceLevelPrices(
      gamma,
      atrRange,
      showAtr,
      expectedMove,
      showExpectedMove,
    );
    // `autoscaleInfoProvider` only re-runs when the library itself decides
    // to recompute the visible range — genuinely new/changed series data,
    // a pan/zoom, or a resize. A prop change alone (a fresh gamma
    // recalculation with no new candle yet, or toggling the ATR checkbox)
    // doesn't trigger that on its own. Confirmed live against the real
    // library (not the mocked one) that `priceScale.setAutoScale(false)`
    // then `(true)` does NOT force it either — the visible range stayed
    // stale through that call. Re-feeding the series its own unchanged
    // candle data is a no-op data-wise, but it's what actually makes the
    // library re-run `autoscaleInfoProvider` with the freshly updated ref
    // (verified the same way: staleness before, correct range after).
    const series = seriesRef.current;
    if (series) setSeriesData(chartRef.current, series, withSessionOpenAnchor(candles, sessionOpenSecondsRef.current));
    // `candles` is deliberately omitted below: it's read here only for its
    // current value, to nudge autoscale when the reference levels change.
    // Candle *updates* are handled by the separate effect above via
    // `series.update()`, not this one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gamma, atrRange, showAtr, expectedMove, showExpectedMove]);


  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || levelMode !== "historical" || history.length === 0) return;
    const levelSeries = HISTORICAL_LEVELS.map((level) => {
      const line = chart.addSeries(LineSeries, {
        color: level.color,
        lineWidth: 2,
        title: level.title,
        priceLineVisible: false,
        lastValueVisible: true,
        // Holds each level flat at its own value between snapshots and
        // jumps vertically when it changes, instead of a diagonal line
        // interpolating between them -- these levels stay constant for
        // long stretches then move in discrete steps (a new gamma
        // aggregate cycle recomputing a different strike), so a straight
        // interpolated line drew a slope that never actually existed and
        // made Call Wall/Gamma Flip/Put Wall cross and overlap each other
        // illegibly whenever two of them swapped relative order.
        lineType: LineType.WithSteps,
      });
      // Same null-price crash as the static-mode price lines above
      // (item.gamma_flip can legitimately be null) -- setData() asserts
      // every value is a number and throws otherwise, so points with a
      // null value for this level are dropped rather than plotted.
      //
      // dedupeAscendingByTime (already used for VWAP below) also applies
      // here: confirmed live, 2026-09 investigation -- two genuinely
      // separate GammaAggregate rows, saved ~373ms apart (SPY,
      // 2026-09-02 20:39:38.056402 and .429345 ET, both real inserts
      // with byte-identical computed values, not a query/merge
      // artifact), collapse to the exact same whole-second bucket once
      // floored to UTCTimestamp -- lightweight-charts requires strictly
      // ascending, unique-per-point time and throws otherwise.
      guardedSetData(
        line,
        dedupeAscendingByTime(
          history
            // Do NOT filter on item.has_data here: every /gamma/{symbol}/history row comes back with has_data=false because
            // the history query does not load the per-strike items has_data is derived from (found 2026-10-09: 103 of 103
            // tactical rows false while call_wall, gamma_flip and put_wall were present), so that filter dropped every
            // point and no colored trace was drawn since PR #186. The honest-empty Tactical case has no levels at all
            // (null or 0), which this filter drops per point.
            .filter((item) => item[level.field] !== null && (item[level.field] as number) > 0)
            .map((item) => ({
              time: Math.floor(new Date(item.as_of).getTime() / 1000) as UTCTimestamp,
              value: item[level.field] as number,
            })),
        ),
        `history-${level.field}`,
      );
      return line;
    });
    return () => {
      if (chartRef.current !== chart) return;
      levelSeries.forEach((line) => chart.removeSeries(line));
    };
  }, [history, levelMode]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !showVwap || vwapPoints.length === 0) return;
    const line = chart.addSeries(LineSeries, {
      color: VWAP_COLOR,
      lineWidth: 2,
      lineStyle: LineStyle.Solid,
      title: vwapProxySymbol
        ? t.priceChart.vwapProxyLabel(vwapProxySymbol)
        : t.priceChart.vwapAnchoredLabel,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    // Raw VWAP points arrive at poll/tick resolution (a point every few
    // seconds) -- feeding that directly to a LineSeries sharing this
    // chart's timeScale with the 1-minute candlestick series inflates the
    // shared logical-index space by roughly the ratio of the two
    // resolutions, which silently breaks fitContent() for the whole chart:
    // confirmed live, 2026-09-18, AAPL/SPY collapsing to showing only the
    // most recent ~15-60 minutes of an otherwise-complete day. Downsampled
    // here, not upstream in dashboard.tsx, so this component can't
    // regress this again regardless of what a future caller passes it.
    guardedSetData(
      line,
      dedupeAscendingByTime(
        aggregateVwapPoints(vwapPoints, timeframe).map((point) => ({
          time: Math.floor(Date.parse(point.timestamp) / 1000) as UTCTimestamp,
          value: point.value,
        })),
      ),
      "vwap",
    );
    return () => {
      if (chartRef.current !== chart) return;
      chart.removeSeries(line);
    };
    // `t` is included so the chart-native legend title (lightweight-charts
    // renders it on its own canvas, not as React JSX) picks up a language
    // switch by recreating the series — the title can't be patched in
    // place without also re-touching `applyOptions` bookkeeping here.
  }, [vwapPoints, showVwap, vwapProxySymbol, t, timeframe]);

  useEffect(() => {
    const series = seriesRef.current;
    const levels = showAtr ? atrLevels(atrRange) : null;

    const recompute = () => {
      if (!series || !levels || !hasPriceRange(candles)) {
        setAtrRects({ upper: null, lower: null });
        return;
      }
      setAtrRects({
        upper: levelRect(series, levels.upper),
        lower: levelRect(series, levels.lower),
      });
    };

    recomputeBandRectsRef.current = recompute;
    recompute();

    const chart = chartRef.current;
    const timeScale = chart?.timeScale();
    timeScale?.subscribeVisibleLogicalRangeChange(recompute);
    return () => {
      if (chartRef.current === chart) {
        timeScale?.unsubscribeVisibleLogicalRangeChange(recompute);
      }
      recomputeBandRectsRef.current = () => {};
    };
  }, [atrRange, showAtr, candles]);


  // The countdown of the forming candle: a second label on the price axis under the last-price tag. The primitive reads the refs while the chart draws (it follows every tick); a one second
  // timer only asks for the redraw that moves the digits.
  const lastOpenRef = useRef<number | null>(null);
  useEffect(() => {
    const series = seriesRef.current;
    if (!candleCountdown || !series) return;
    const primitive = new CandleCountdownPrimitive(() => {
      const left = candleSecondsLeft(lastCandleTimeRef.current, timeframe, Date.now() / 1000);
      const close = lastCloseRef.current;
      return { price: close, secondsText: left === null ? null : formatCountdown(left), up: close === null || lastOpenRef.current === null || close >= lastOpenRef.current };
    });
    series.attachPrimitive(primitive);
    const interval = window.setInterval(() => primitive.refresh(), 1000);
    return () => {
      window.clearInterval(interval);
      if (seriesRef.current === series) series.detachPrimitive(primitive);
    };
  }, [candleCountdown, timeframe, symbol]);

  return (
    <section className="panel price-chart-panel" aria-labelledby="price-chart-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">{t.priceChart.eyebrow}</p>
          <h2 id="price-chart-title">
            {t.priceChart.title(symbol, t.priceChart.timeframeLabels[timeframe])}
          </h2>
          {gammaView !== "structural" && !gamma.has_data && (
            <p className="no-tactical-data-notice">{t.dashboard.noTacticalDataLabel}</p>
          )}
        </div>
        <div className="chart-controls">
          <fieldset className="level-mode-selector" aria-label={t.priceChart.levelModeAriaLabel}>
            <legend>{t.priceChart.levelsLegend}</legend>
            <button
              type="button"
              aria-pressed={levelMode === "static"}
              onClick={() => setLevelMode("static")}
            >
              {t.priceChart.staticButton}
            </button>
            <button
              type="button"
              aria-pressed={levelMode === "historical"}
              onClick={() => setLevelMode("historical")}
            >
              {t.priceChart.historicalButton}
            </button>
          </fieldset>
          <fieldset className="overlay-toggles" aria-label={t.priceChart.overlaysAriaLabel}>
            <legend>{t.priceChart.overlaysLegend}</legend>
            <label
              className="chart-toggle"
              title={vwapNotApplicable ? t.priceChart.vwapNotApplicableLabel : undefined}
            >
              <input
                type="checkbox"
                checked={!vwapNotApplicable && showVwap}
                disabled={vwapNotApplicable}
                onChange={(event) => setShowVwap(event.target.checked)}
              />
              {vwapNotApplicable
                ? t.priceChart.vwapNotApplicableLabel
                : vwapProxySymbol
                  ? t.priceChart.vwapProxyLabel(vwapProxySymbol)
                  : t.priceChart.vwapAnchoredLabel}
            </label>
            <label className="chart-toggle">
              <input
                type="checkbox"
                checked={showAtr}
                onChange={(event) => setShowAtr(event.target.checked)}
              />
              {t.priceChart.atrRangeLabel}
            </label>
            <label className="chart-toggle">
              <input
                type="checkbox"
                checked={showExpectedMove}
                onChange={(event) => setShowExpectedMove(event.target.checked)}
              />
              {t.priceChart.expectedMoveRangeLabel}
            </label>
            <label className="chart-toggle" title={t.priceChart.flowTitle}>
              <input type="checkbox" checked={showFlow} onChange={(event) => setShowFlow(event.target.checked)} />
              {t.priceChart.flowLabel}
            </label>
            <EmaMenu emas={emas} convergingIds={convergingEmas} onChange={setEmas} />
            <label className="chart-toggle">
              <input
                type="checkbox"
                checked={showProfile}
                onChange={(event) => setShowProfile(event.target.checked)}
              />
              {t.priceChart.gexHistogramLabel}
            </label>
            {showProfile && (
              <label className="chart-toggle">
                <input
                  type="checkbox"
                  checked={showSmile}
                  onChange={(event) => setShowSmile(event.target.checked)}
                />
                {t.priceChart.smileLabel}
              </label>
            )}
            {showProfile && showSmile && smileExpirations.length > 1 && (
              <select
                aria-label={t.priceChart.smileExpirationAriaLabel}
                value={smileExpiration}
                onChange={(event) => setSmileChoice({ symbol, date: event.target.value })}
              >
                <option value="">{t.priceChart.smileExpirationNearest}</option>
                {smileExpirations.map((date) => (
                  <option key={date} value={date}>
                    {date}
                  </option>
                ))}
              </select>
            )}
            <label className="chart-toggle">
              <input
                type="checkbox"
                checked={showHeatmap}
                onChange={(event) => setShowHeatmap(event.target.checked)}
              />
              {t.priceChart.heatmapLabel}
            </label>
            {showHeatmap && (
              <div className="chart-seg" role="group" aria-label={t.priceChart.heatmapMetricAriaLabel}>
                {HEATMAP_METRIC_TABS.map((tab) => (
                  <button
                    key={tab.metric}
                    type="button"
                    aria-pressed={!sidesOn && heatmapMetric === tab.metric}
                    title={
                      tab.metric === "absolute_gamma"
                        ? t.priceChart.heatmapMetricAbsoluteTitle
                        : tab.metric === "total_gamma"
                          ? t.priceChart.heatmapMetricTotalTitle
                          : undefined
                    }
                    onClick={() => {
                      setHeatmapMetric(tab.metric);
                      setSideLayers({ calls: false, puts: false });
                    }}
                  >
                    {t.priceChart[tab.labelKey]}
                  </button>
                ))}
              </div>
            )}
            {showHeatmap && (
              <label className="chart-toggle" title={t.priceChart.heatmapDayScaleTitle}>
                <input type="checkbox" checked={dayScale} onChange={(event) => setDayScale(event.target.checked)} />
                {t.priceChart.heatmapDayScaleLabel}
              </label>
            )}
            {showHeatmap && (
              <div className="chart-layers" role="group" aria-label={t.priceChart.heatmapSidesAriaLabel} title={t.priceChart.heatmapSidesTitle}>
                <label className="chart-toggle">
                  <input
                    type="checkbox"
                    checked={sideLayers.calls}
                    onChange={(event) => setSideLayers((current) => ({ ...current, calls: event.target.checked }))}
                  />
                  {t.priceChart.heatmapSideCalls}
                </label>
                <label className="chart-toggle">
                  <input
                    type="checkbox"
                    checked={sideLayers.puts}
                    onChange={(event) => setSideLayers((current) => ({ ...current, puts: event.target.checked }))}
                  />
                  {t.priceChart.heatmapSidePuts}
                </label>
              </div>
            )}
            <label className="chart-toggle">
              <input
                type="checkbox"
                checked={showAlertCubes}
                onChange={(event) => setShowAlertCubes(event.target.checked)}
              />
              {t.priceChart.alertCubesLabel}
            </label>
            {showAlertCubes && (
              <>
                <span className="cube-min">
                  <span className="cube-min-prefix" aria-hidden="true">
                    {"≥"}
                  </span>
                  <input
                    type="text"
                    inputMode="numeric"
                    className="cube-min-input"
                    aria-label={t.priceChart.alertCubesMinAriaLabel}
                    title={t.priceChart.alertCubesMinTitle(cubeMin.source, cubeMinDeq)}
                    maxLength={9}
                    value={cubeMinDraft && cubeMinDraft.symbol === symbol ? cubeMinDraft.text : String(cubeMinDeq)}
                    onChange={(event) => setCubeMinDraft({ symbol, text: event.target.value })}
                    onBlur={commitCubeMinDraft}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") commitCubeMinDraft();
                      if (event.key === "Escape") setCubeMinDraft(null);
                    }}
                  />
                  <button type="button" className="cube-min-button" onClick={() => setCubeMinFor(Math.max(1, Math.round(cubeMinDeq / 2)))}>
                    {t.priceChart.alertCubesHalf}
                  </button>
                  <button type="button" className="cube-min-button" onClick={() => setCubeMinFor(Math.min(CUBE_MIN_LIMIT, cubeMinDeq * 2))}>
                    {t.priceChart.alertCubesDouble}
                  </button>
                  {cubeMin.source === "custom" && (
                    <button type="button" className="cube-min-button" title={t.priceChart.alertCubesAutoTitle} onClick={() => setCubeMinFor(null)}>
                      {t.priceChart.alertCubesAuto}
                    </button>
                  )}
                </span>
                <label className="chart-toggle">
                  <input
                    type="checkbox"
                    checked={cubeShowFar}
                    onChange={(event) => setCubeShowFar(event.target.checked)}
                  />
                  {t.priceChart.alertCubesFarLabel}
                </label>
                <label className="chart-toggle" title={t.priceChart.alertCubesNetTitle}>
                  <input
                    type="checkbox"
                    checked={cubeSizing === "net"}
                    onChange={(event) => setCubeSizing(event.target.checked ? "net" : "gross")}
                  />
                  {t.priceChart.alertCubesNetLabel}
                </label>
              </>
            )}
          </fieldset>
          <fieldset
            className="level-mode-selector drawing-tools"
            aria-label={t.priceChart.drawingToolsAriaLabel}
          >
            <legend>{t.priceChart.drawingToolsLegend}</legend>
            <button
              type="button"
              aria-pressed={drawMode}
              onClick={() => setDrawMode((current) => !current)}
            >
              {t.priceChart.trendlineButton}
            </button>
            <button
              type="button"
              disabled={!trendline}
              onClick={() => setTrendline(null)}
            >
              {t.priceChart.clearTrendlineButton}
            </button>
          </fieldset>
          <span className="mode-pill">{t.dashboard.liveButton}</span>
          {streamStatus === "fallback" && (
            <span
              className="stream-fallback-indicator"
              role="status"
              title={t.priceChart.streamFallbackLabel}
              aria-label={t.priceChart.streamFallbackLabel}
            />
          )}
        </div>
      </div>
      {showFlow && openingFlow && (
        <div className="opening-flow-strip" role="status" title={t.priceChart.openingFlowHint}>
          <strong>{t.priceChart.openingFlowLabel}</strong>
          <span className={openingFlow.calls >= 0 ? "opening-flow-up" : "opening-flow-down"}>
            Calls {formatDeltaFlow(openingFlow.calls)} Δ
          </span>
          <span className={openingFlow.puts >= 0 ? "opening-flow-up" : "opening-flow-down"}>
            Puts {formatDeltaFlow(openingFlow.puts)} Δ
          </span>
          <span className={openingFlow.net >= 0 ? "opening-flow-up" : "opening-flow-down"}>
            {t.priceChart.openingFlowNet} {formatDeltaFlow(openingFlow.net)} Δ
          </span>
          {openingFlow.classifiedShare !== null && (
            <span className="opening-flow-dim">
              {Math.round(openingFlow.classifiedShare * 100)}% {t.priceChart.openingFlowClassified}
            </span>
          )}
          {openingAlerts && (
            <span className="opening-flow-alerts">
              {t.priceChart.openingFlowAlerts}:{" "}
              <span className="opening-flow-up">
                {formatPremium(openingAlerts.bullishPremium)} {t.priceChart.openingFlowBullish} ({openingAlerts.bullishCount})
              </span>{" "}
              <span className="opening-flow-down">
                {formatPremium(openingAlerts.bearishPremium)} {t.priceChart.openingFlowBearish} ({openingAlerts.bearishCount})
              </span>{" "}
              <span className="opening-flow-dim">
                {openingAlerts.unclearCount} {t.priceChart.openingFlowUnclear}
              </span>
            </span>
          )}
        </div>
      )}
      <div className="price-chart-frame">
        <div
          ref={containerRef}
          className={`price-chart${drawMode ? " price-chart-drawing" : ""}`}
          aria-label={t.priceChart.chartAriaLabel(symbol)}
        />
        {atrRects.upper && (
          <div
            className="atr-band"
            style={{ top: atrRects.upper.top, height: atrRects.upper.height }}
            aria-hidden="true"
          />
        )}
        {atrRects.lower && (
          <div
            className="atr-band"
            style={{ top: atrRects.lower.top, height: atrRects.lower.height }}
            aria-hidden="true"
          />
        )}
        {showProfile && showSmile && hoveredSmile && (
          <div className="alert-cube-tooltip" role="tooltip" style={{ left: hoveredSmile.x - 14, top: hoveredSmile.y + 14, transform: "translateX(-100%)" }}>
            {t.priceChart.smileTooltip(
              hoveredSmile.point.strike,
              hoveredSmile.point.type,
              `${(hoveredSmile.point.iv * 100).toFixed(1)} %`,
              smileExpiration || smileExpirations[0] || "",
            )}
          </div>
        )}
        {showAlertCubes && hoveredCube && (() => {
          const hovered = cubes.find((cube) => cube.id === hoveredCube.id);
          if (!hovered) return null;
          return (
            <AlertCubeTooltip
              cube={hovered}
              price={candles.at(-1)?.close ?? null}
              t={t.priceChart}
              style={{ left: hoveredCube.x + 14, top: hoveredCube.y + 14 }}
            />
          );
        })()}
        <Image
          src="/logo-watermark.png"
          alt=""
          width={360}
          height={215}
          className="chart-watermark"
          aria-hidden="true"
        />
      </div>
      {!candles.length && <p className="chart-empty">{t.priceChart.emptyState}</p>}
    </section>
  );
}
