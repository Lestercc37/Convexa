"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { BarSeries, BaselineSeries, ColorType, createChart, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import { getContractChart } from "@/lib/api";
import { useLanguage } from "@/lib/i18n/language-context";
import { EASTERN_TIME_ZONE } from "@/lib/market-session";
import type { ContractChartResponse, ContractPoint, ContractSeries } from "@/lib/types";

/**
 * Call | Put of the same 0DTE strike in two separate charts side by side (Lester 2026-10-30, ThinkorSwim-style): the vertical axis is the
 * dollars ONE contract gained or lost since the first minute ((mid - first mid) x 100), green above zero, red below. Data: GET
 * /contracts/{symbol}/minutes (option_quote_minutes, the stream's last bid/ask of each minute). Thin bars behind the line: the high/low
 * inside each minute (Lester 2026-11-02: the line alone drew SPXW 11-02 P7620's peak $161 under the real one).
 */
const REFRESH_MS = 60_000;

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const price = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function etTime(seconds: number): string {
  return new Intl.DateTimeFormat("es-US", { timeZone: EASTERN_TIME_ZONE, hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(seconds * 1000));
}

function todayEt(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: EASTERN_TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
}

const toSeconds = (iso: string) => Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;

/** The point with the day's highest price inside a minute (null for an empty series). */
export function dayHigh(points: ContractPoint[]): ContractPoint | null {
  return points.reduce<ContractPoint | null>((best, p) => (best === null || p.pnl_high > best.pnl_high ? p : best), null);
}

/** One thin bar per minute whose range went beyond the line (open = close = the line's value), colored by the side of zero. */
export function rangeBars(points: ContractPoint[]) {
  return points
    .filter((p) => p.pnl_high > p.pnl || p.pnl_low < p.pnl)
    .map((p) => ({
      time: toSeconds(p.time),
      open: p.pnl,
      high: p.pnl_high,
      low: p.pnl_low,
      close: p.pnl,
      color: p.pnl >= 0 ? "rgba(0, 220, 90, 0.55)" : "rgba(250, 0, 10, 0.55)",
    }));
}

function ContractPane({ series, label }: { series: ContractSeries | null; label: string }) {
  const { t } = useLanguage();
  const container = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Baseline"> | null>(null);
  const rangeRef = useRef<ISeriesApi<"Bar"> | null>(null);
  // The whole session stays in view (refit on every refresh and resize) until the user zooms or pans.
  const userAdjusted = useRef(false);

  useEffect(() => {
    if (!container.current) return;
    const chart = createChart(container.current, {
      autoSize: true,
      // Same as the main price chart: no TradingView logo, Convexa's own watermark below (attribution stays where the main chart keeps it).
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#8a93a0",
        fontFamily: "JetBrains Mono, monospace",
        fontSize: 11,
        attributionLogo: false,
      },
      grid: { vertLines: { color: "rgba(255,255,255,0.04)" }, horzLines: { color: "rgba(255,255,255,0.04)" } },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.09)" },
      // lockVisibleTimeRangeOnResize: autoSize's first resize otherwise keeps the bar spacing of the empty chart and shows only part of the day.
      timeScale: {
        borderColor: "rgba(255,255,255,0.09)",
        timeVisible: true,
        secondsVisible: false,
        lockVisibleTimeRangeOnResize: true,
        tickMarkFormatter: (time: UTCTimestamp) => etTime(time),
      },
      localization: { timeFormatter: (time: UTCTimestamp) => etTime(time), priceFormatter: (value: number) => usd.format(value) },
    });
    rangeRef.current = chart.addSeries(BarSeries, { thinBars: true, openVisible: false, lastValueVisible: false, priceLineVisible: false });
    seriesRef.current = chart.addSeries(BaselineSeries, {
      baseValue: { type: "price", price: 0 },
      topLineColor: "#00dc5a",
      topFillColor1: "rgba(0, 220, 90, 0.28)",
      topFillColor2: "rgba(0, 220, 90, 0.04)",
      bottomLineColor: "#fa000a",
      bottomFillColor1: "rgba(250, 0, 10, 0.04)",
      bottomFillColor2: "rgba(250, 0, 10, 0.28)",
      lineWidth: 2,
    });
    chartRef.current = chart;
    const element = container.current;
    const markAdjusted = () => {
      userAdjusted.current = true;
    };
    element.addEventListener("wheel", markAdjusted, { passive: true });
    element.addEventListener("pointerdown", markAdjusted);
    const observer = new ResizeObserver(() => {
      if (!userAdjusted.current) chart.timeScale().fitContent();
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
      element.removeEventListener("wheel", markAdjusted);
      element.removeEventListener("pointerdown", markAdjusted);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      rangeRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!seriesRef.current || !rangeRef.current) return;
    const points = series?.points ?? [];
    rangeRef.current.setData(rangeBars(points));
    seriesRef.current.setData(points.map((p) => ({ time: toSeconds(p.time), value: p.pnl })));
    if (!userAdjusted.current) chartRef.current?.timeScale().fitContent();
  }, [series]);

  const first = series?.points[0] ?? null;
  const last = series?.points.at(-1) ?? null;
  const high = dayHigh(series?.points ?? []);
  return (
    <section className="v2-contract-pane">
      <header>
        <b className={label === "Call" ? "v2-up" : "v2-down"}>{label}</b>
        <span className="v2-dim">{series?.occ_symbol ?? "—"}</span>
        {last && (
          <>
            <span>
              {t.contractChart.mid} {price.format(last.mid)}
            </span>
            <span className={last.pnl >= 0 ? "v2-up" : "v2-down"}>
              {last.pnl >= 0 ? "+" : ""}
              {usd.format(last.pnl)}
            </span>
            {first && <span className="v2-dim">{t.contractChart.since(price.format(first.mid), etTime(toSeconds(first.time)))}</span>}
            {high && (
              <span className="v2-dim">
                {t.contractChart.dayHigh} {high.pnl_high >= 0 ? "+" : ""}
                {usd.format(high.pnl_high)} ({etTime(toSeconds(high.time))})
              </span>
            )}
          </>
        )}
      </header>
      <div className="v2-contract-frame">
        <div ref={container} className="v2-contract-chart" aria-label={`${label} ${t.contractChart.sinceFirst}`} />
        <Image src="/logo-watermark.png" alt="" width={360} height={215} className="chart-watermark" aria-hidden="true" />
      </div>
    </section>
  );
}

export function ContractChartPanel({ symbol }: { symbol: string }) {
  const { t } = useLanguage();
  const [strike, setStrike] = useState<number | null>(null);
  const [data, setData] = useState<ContractChartResponse | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const load = () =>
      getContractChart(symbol, { strike: strike ?? undefined }, controller.signal)
        .then((response) => {
          setData(response);
          setFailed(false);
        })
        .catch(() => {
          if (!controller.signal.aborted) setFailed(true);
        });
    void load();
    const interval = window.setInterval(() => {
      // Only today's session still moves; an earlier day is final.
      if (!data || data.session_date === todayEt()) void load();
    }, REFRESH_MS);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, strike]);

  return (
    <div className="v2-contract">
      <div className="v2-chain-head">
        <label className="v2-dim">
          {t.contractChart.strike}{" "}
          <select
            className="v2-sel"
            aria-label={t.contractChart.strike}
            value={data?.strike ?? ""}
            onChange={(event) => setStrike(Number(event.target.value))}
            disabled={!data || data.strikes.length === 0}
          >
            {(data?.strikes ?? []).map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>
        {data && data.strikes.length > 0 && <span className="v2-dim">{t.contractChart.sessionOf(data.session_date)}</span>}
        {data && <span className="v2-dim">{data.root}</span>}
      </div>
      {failed && !data ? (
        <p className="v2-panel-note">No se pudieron leer los datos; reintentando.</p>
      ) : !data ? (
        <p className="v2-panel-note">Cargando…</p>
      ) : data.strikes.length === 0 ? (
        <p className="v2-panel-note">{t.contractChart.empty}</p>
      ) : (
        <div className="v2-contract-pair">
          <ContractPane series={data.call} label={t.contractChart.call} />
          <ContractPane series={data.put} label={t.contractChart.put} />
        </div>
      )}
      <p className="v2-chain-hint">{t.contractChart.hint}</p>
    </div>
  );
}
