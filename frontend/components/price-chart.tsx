"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  LineSeries,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { getGammaHistory } from "@/lib/api";
import type { MinuteCandle, VwapPoint } from "@/lib/candles";
import type { AtrRange, GammaHistoryItem, GammaResponse } from "@/lib/types";
import { LEVEL_MERGE_THRESHOLD } from "./gravity-map";

type PriceChartProps = {
  symbol: string;
  candles: MinuteCandle[];
  gamma: GammaResponse;
  vwapPoints?: VwapPoint[];
  atrRange?: AtrRange;
};

type GammaLevel = {
  price: number;
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

function gammaLevels(gamma: GammaResponse): GammaLevel[] {
  const range = gamma.call_wall - gamma.put_wall;
  const mergeFlipAndAbsolute =
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

function chartCandle(candle: MinuteCandle) {
  return { ...candle, time: candle.time as UTCTimestamp };
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

type AtrBandValues = {
  outerUpper: number;
  outerLower: number;
  innerUpper: number;
  innerLower: number;
};

function atrBands(atrRange: AtrRange | undefined): AtrBandValues | null {
  if (!atrRange || atrRange.bands_provisional) return null;
  const { outer_upper_band, outer_lower_band, inner_upper_band, inner_lower_band } = atrRange;
  if (
    outer_upper_band === null ||
    outer_lower_band === null ||
    inner_upper_band === null ||
    inner_lower_band === null
  ) {
    return null;
  }
  return {
    outerUpper: outer_upper_band,
    outerLower: outer_lower_band,
    innerUpper: inner_upper_band,
    innerLower: inner_lower_band,
  };
}

function bandRect(
  series: ISeriesApi<"Candlestick">,
  upperPrice: number,
  lowerPrice: number,
): BandRect | null {
  const top = series.priceToCoordinate(upperPrice);
  const bottom = series.priceToCoordinate(lowerPrice);
  if (top === null || bottom === null) return null;
  return { top, height: bottom - top };
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

export function PriceChart({
  symbol,
  candles,
  gamma,
  vwapPoints = [],
  atrRange,
}: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const initialCandlesRef = useRef(candles);
  const recomputeBandRectsRef = useRef<() => void>(() => {});
  const [levelMode, setLevelMode] = useState<LevelMode>("static");
  const [history, setHistory] = useState<GammaHistoryItem[]>([]);
  const [showVwap, setShowVwap] = useState(true);
  const [showAtr, setShowAtr] = useState(true);
  const [bandRects, setBandRects] = useState<{ outer: BandRect | null; inner: BandRect | null }>({
    outer: null,
    inner: null,
  });

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

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
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#787b86",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "rgba(42, 46, 57, 0.6)" },
        horzLines: { color: "rgba(42, 46, 57, 0.6)" },
      },
      timeScale: { timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderColor: "#2A2E39" },
    });
    // TradingView-native candle colors — never the Convexa brand pair above,
    // which is reserved for what Convexa itself calculates (Gamma levels).
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#26A69A",
      downColor: "#EF5350",
      borderVisible: false,
      wickUpColor: "#26A69A",
      wickDownColor: "#EF5350",
    });
    series.setData(initialCandlesRef.current.map(chartCandle));
    chart.timeScale().fitContent();
    chartRef.current = chart;
    seriesRef.current = series;

    const handleSizeChange = () => recomputeBandRectsRef.current();
    chart.timeScale().subscribeSizeChange(handleSizeChange);

    return () => {
      chart.timeScale().unsubscribeSizeChange(handleSizeChange);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    const latest = candles.at(-1);
    if (latest) seriesRef.current?.update(chartCandle(latest));
  }, [candles]);

  useEffect(() => {
    if (levelMode !== "historical") return;
    const controller = new AbortController();
    getGammaHistory(symbol, controller.signal)
      .then(({ items }) => setHistory(items))
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          console.error("No se pudo cargar el histórico de niveles", reason);
        }
      });
    return () => controller.abort();
  }, [levelMode, symbol]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series || levelMode !== "static") return;
    const lines: IPriceLine[] = gammaLevels(gamma).map((level) =>
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
      });
      line.setData(
        history.map((item) => ({
          time: Math.floor(new Date(item.as_of).getTime() / 1000) as UTCTimestamp,
          value: item[level.field],
        })),
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
      title: "VWAP Anclado",
      priceLineVisible: false,
      lastValueVisible: true,
    });
    line.setData(
      dedupeAscendingByTime(
        [...vwapPoints]
          .sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp))
          .map((point) => ({
            time: Math.floor(Date.parse(point.timestamp) / 1000) as UTCTimestamp,
            value: point.value,
          })),
      ),
    );
    return () => {
      if (chartRef.current !== chart) return;
      chart.removeSeries(line);
    };
  }, [vwapPoints, showVwap]);

  useEffect(() => {
    const series = seriesRef.current;
    const bands = showAtr ? atrBands(atrRange) : null;

    const recompute = () => {
      if (!series || !bands || !hasPriceRange(candles)) {
        setBandRects({ outer: null, inner: null });
        return;
      }
      setBandRects({
        outer: bandRect(series, bands.outerUpper, bands.outerLower),
        inner: bandRect(series, bands.innerUpper, bands.innerLower),
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

  return (
    <section className="panel price-chart-panel" aria-labelledby="price-chart-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Precio intradía · memoria local</p>
          <h2 id="price-chart-title">{symbol} · Velas de 1 minuto</h2>
        </div>
        <div className="chart-controls">
          <fieldset className="level-mode-selector" aria-label="Modo de niveles">
            <legend>Niveles:</legend>
            <button
              type="button"
              aria-pressed={levelMode === "static"}
              onClick={() => setLevelMode("static")}
            >
              Estático
            </button>
            <button
              type="button"
              aria-pressed={levelMode === "historical"}
              onClick={() => setLevelMode("historical")}
            >
              Histórico
            </button>
          </fieldset>
          <fieldset className="overlay-toggles" aria-label="Overlays">
            <legend>Overlays:</legend>
            <label className="chart-toggle">
              <input
                type="checkbox"
                checked={showVwap}
                onChange={(event) => setShowVwap(event.target.checked)}
              />
              VWAP Anclado
            </label>
            <label className="chart-toggle">
              <input
                type="checkbox"
                checked={showAtr}
                onChange={(event) => setShowAtr(event.target.checked)}
              />
              Rango ATR
            </label>
          </fieldset>
          <span className="mode-pill">En vivo</span>
        </div>
      </div>
      <div className="price-chart-frame">
        <div
          ref={containerRef}
          className="price-chart"
          aria-label={`Chart de velas para ${symbol}`}
        />
        {bandRects.outer && (
          <div
            className="atr-band atr-band-outer"
            style={{ top: bandRects.outer.top, height: bandRects.outer.height }}
            aria-hidden="true"
          />
        )}
        {bandRects.inner && (
          <div
            className="atr-band atr-band-inner"
            style={{ top: bandRects.inner.top, height: bandRects.inner.height }}
            aria-hidden="true"
          />
        )}
        <Image
          src="/logo-watermark.png"
          alt=""
          width={360}
          height={215}
          className="chart-watermark"
          aria-hidden="true"
        />
      </div>
      {!candles.length && <p className="chart-empty">Esperando la primera muestra de precio…</p>}
    </section>
  );
}
