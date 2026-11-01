"use client";

import { useEffect, useRef, useState } from "react";
import { BaselineSeries, ColorType, createChart, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import { getContractChart } from "@/lib/api";
import { useLanguage } from "@/lib/i18n/language-context";
import { EASTERN_TIME_ZONE } from "@/lib/market-session";
import type { ContractChartResponse, ContractSeries } from "@/lib/types";

/**
 * Call | Put of the same 0DTE strike in two separate charts side by side (Lester 2026-10-30, ThinkorSwim-style): the vertical axis is the
 * dollars ONE contract gained or lost since the first minute ((mid - first mid) x 100), green above zero, red below. Data: GET
 * /contracts/{symbol}/minutes (option_quote_minutes, the stream's last bid/ask of each minute).
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

function ContractPane({ series, label }: { series: ContractSeries | null; label: string }) {
  const { t } = useLanguage();
  const container = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Baseline"> | null>(null);

  useEffect(() => {
    if (!container.current) return;
    const chart = createChart(container.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8a93a0", fontFamily: "JetBrains Mono, monospace", fontSize: 11 },
      grid: { vertLines: { color: "rgba(255,255,255,0.04)" }, horzLines: { color: "rgba(255,255,255,0.04)" } },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.09)" },
      timeScale: { borderColor: "rgba(255,255,255,0.09)", timeVisible: true, secondsVisible: false, tickMarkFormatter: (time: UTCTimestamp) => etTime(time) },
      localization: { timeFormatter: (time: UTCTimestamp) => etTime(time), priceFormatter: (value: number) => usd.format(value) },
    });
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
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!seriesRef.current) return;
    const points = (series?.points ?? []).map((p) => ({ time: Math.floor(new Date(p.time).getTime() / 1000) as UTCTimestamp, value: p.pnl }));
    seriesRef.current.setData(points);
    chartRef.current?.timeScale().fitContent();
  }, [series]);

  const last = series?.points.at(-1) ?? null;
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
          </>
        )}
      </header>
      <div ref={container} className="v2-contract-chart" aria-label={`${label} ${t.contractChart.sinceFirst}`} />
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
