"use client";

import Image from "next/image";
import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { MinuteCandle } from "@/lib/candles";
import type { GammaResponse } from "@/lib/types";
import { LEVEL_MERGE_THRESHOLD } from "./gravity-map";

type PriceChartProps = {
  symbol: string;
  candles: MinuteCandle[];
  gamma: GammaResponse;
};

type GammaLevel = {
  price: number;
  title: string;
  color: string;
};

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
    { price: gamma.put_wall, title: "Put Wall", color: "#ff7a45" },
    ...middleLevels,
    { price: gamma.call_wall, title: "Call Wall", color: "#36c99b" },
  ];
}

function chartCandle(candle: MinuteCandle) {
  return { ...candle, time: candle.time as UTCTimestamp };
}

export function PriceChart({ symbol, candles, gamma }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const initialCandlesRef = useRef(candles);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: 420,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#91a0b2",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "rgba(42, 55, 70, 0.42)" },
        horzLines: { color: "rgba(42, 55, 70, 0.42)" },
      },
      timeScale: { timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderColor: "#2a3746" },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#36c99b",
      downColor: "#ff7a45",
      borderVisible: false,
      wickUpColor: "#36c99b",
      wickDownColor: "#ff7a45",
    });
    series.setData(initialCandlesRef.current.map(chartCandle));
    chart.timeScale().fitContent();
    chartRef.current = chart;
    seriesRef.current = series;

    const resizeObserver = new ResizeObserver(([entry]) => {
      chart.applyOptions({ width: entry.contentRect.width });
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
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
    const series = seriesRef.current;
    if (!series) return;
    const lines: IPriceLine[] = gammaLevels(gamma).map((level) =>
      series.createPriceLine({
        ...level,
        lineStyle: LineStyle.Dashed,
        lineWidth: 1,
        axisLabelVisible: true,
      }),
    );
    return () => lines.forEach((line) => series.removePriceLine(line));
  }, [gamma]);

  return (
    <section className="panel price-chart-panel" aria-labelledby="price-chart-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Precio intradía · memoria local</p>
          <h2 id="price-chart-title">{symbol} · Velas de 1 minuto</h2>
        </div>
        <span className="mode-pill">En vivo</span>
      </div>
      <div className="price-chart-frame">
        <div
          ref={containerRef}
          className="price-chart"
          aria-label={`Chart de velas para ${symbol}`}
        />
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
