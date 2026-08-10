"use client";

import Image from "next/image";
import { useCallback, useEffect, useMemo, useState } from "react";
import { getGamma, getMarket, getUnderlyings } from "@/lib/api";
import { aggregateMinuteCandles, type PricePoint, type VwapPoint } from "@/lib/candles";
import { POLLING_INTERVAL_MS } from "@/lib/polling";
import type { GammaResponse, MarketResponse, Underlying } from "@/lib/types";
import { AlertsPanel } from "./alerts-panel";
import { DerivedMetricsBar } from "./derived-metrics-bar";
import { ExpectedMoveWidget } from "./expected-move-widget";
import { PriceChart } from "./price-chart";
import { QuickScreener } from "./quick-screener";
import { RegimeBadge } from "./regime-badge";
import { VolatilitySmile } from "./volatility-smile";

// Only 1-minute candles exist (client-side accumulated, dashboard-spec.md
// section 2.2) — the other buttons are decorative chrome, same as the side
// toolbar, until a real multi-timeframe aggregation exists.
const TIMEFRAMES = ["1m", "5m", "15m", "1h"] as const;

const PRICE_FORMAT = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const EXPOSURE_FORMAT = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});

export function Dashboard() {
  const [underlyings, setUnderlyings] = useState<Underlying[]>([]);
  const [symbol, setSymbol] = useState("");
  const [gamma, setGamma] = useState<GammaResponse | null>(null);
  const [market, setMarket] = useState<MarketResponse | null>(null);
  const [pricePoints, setPricePoints] = useState<PricePoint[]>([]);
  const [vwapPoints, setVwapPoints] = useState<VwapPoint[]>([]);
  const [error, setError] = useState("");
  const candles = useMemo(() => aggregateMinuteCandles(pricePoints), [pricePoints]);
  const latestCandle = candles.at(-1) ?? null;

  useEffect(() => {
    const controller = new AbortController();
    getUnderlyings(controller.signal)
      .then(({ underlyings: items }) => {
        setUnderlyings(items);
        setSymbol((current) => current || items[0]?.symbol || "");
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "No se pudo cargar la lista");
        }
      });
    return () => controller.abort();
  }, []);

  const refresh = useCallback(async (activeSymbol: string, signal?: AbortSignal) => {
    if (!activeSymbol) return;
    try {
      const [gammaData, marketData] = await Promise.all([
        getGamma(activeSymbol, signal),
        getMarket(activeSymbol, signal),
      ]);
      setGamma(gammaData);
      setMarket(marketData);
      setPricePoints((current) => [
        ...current,
        { timestamp: marketData.as_of, price: marketData.price },
      ]);
      const anchoredVwap = marketData.anchored_vwap;
      if (anchoredVwap && !anchoredVwap.provisional && anchoredVwap.value !== null) {
        const value = anchoredVwap.value;
        setVwapPoints((current) =>
          // No live backend scheduler writes market_snapshots yet (see
          // dashboard-spec.md section 2.2), so consecutive polls can return
          // the exact same as_of when no new data has landed. Appending it
          // again would give the chart two points with an identical
          // timestamp, which lightweight-charts rejects (data must be
          // strictly ascending by time).
          current.at(-1)?.timestamp === marketData.as_of
            ? current
            : [...current, { timestamp: marketData.as_of, value }],
        );
      }
      setError("");
    } catch (reason: unknown) {
      if (!signal?.aborted) {
        setError(reason instanceof Error ? reason.message : "No se pudieron cargar los datos");
      }
    }
  }, []);

  useEffect(() => {
    if (!symbol) return;
    const controller = new AbortController();
    const initialRefresh = window.setTimeout(
      () => void refresh(symbol, controller.signal),
      0,
    );
    const interval = window.setInterval(() => void refresh(symbol), POLLING_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearTimeout(initialRefresh);
      window.clearInterval(interval);
    };
  }, [refresh, symbol]);

  return (
    <main className="tv-shell">
      <header className="tv-topbar">
        <div className="tv-topbar-left">
          <Image
            src="/logo-header.png"
            alt="Convexa — Volatility Exposure Edge"
            width={96}
            height={26}
            className="tv-logo"
            priority
          />
          <label className="tv-symbol-control">
            <span className="sr-only">Underlying</span>
            <select
              value={symbol}
              onChange={(event) => {
                setGamma(null);
                setMarket(null);
                setPricePoints([]);
                setVwapPoints([]);
                setSymbol(event.target.value);
              }}
              disabled={!underlyings.length}
            >
              {underlyings.map((item) => (
                <option key={item.symbol} value={item.symbol}>
                  {item.symbol}
                </option>
              ))}
            </select>
          </label>
          {market && (
            <div className="tv-price-readout">
              <strong>{PRICE_FORMAT.format(market.price)}</strong>
              {latestCandle && (
                <span className="tv-ohlc">
                  O {PRICE_FORMAT.format(latestCandle.open)} H{" "}
                  {PRICE_FORMAT.format(latestCandle.high)} L{" "}
                  {PRICE_FORMAT.format(latestCandle.low)} C{" "}
                  {PRICE_FORMAT.format(latestCandle.close)}
                </span>
              )}
            </div>
          )}
        </div>
        <div className="tv-timeframes" role="group" aria-label="Timeframe">
          {TIMEFRAMES.map((timeframe) => (
            <button
              key={timeframe}
              type="button"
              className="tv-timeframe"
              aria-pressed={timeframe === "1m"}
              disabled={timeframe !== "1m"}
            >
              {timeframe}
            </button>
          ))}
        </div>
        <div className="tv-topbar-right">
          {gamma && market && <RegimeBadge gamma={gamma} market={market} />}
        </div>
      </header>

      {error ? (
        <section className="panel status error" role="alert">
          {error}
        </section>
      ) : gamma && market ? (
        <div className="tv-body">
          <aside className="tv-toolbar" aria-hidden="true">
            <span className="tv-tool-icon">↖</span>
            <span className="tv-tool-icon">✎</span>
            <span className="tv-tool-icon">▭</span>
            <span className="tv-tool-icon">〰</span>
            <span className="tv-tool-icon">Ⓣ</span>
          </aside>

          <div className="tv-center">
            <PriceChart
              key={`price-chart-${symbol}`}
              symbol={symbol}
              candles={candles}
              gamma={gamma}
              vwapPoints={vwapPoints}
              atrRange={market.atr_range}
            />
          </div>

          <aside className="tv-sidebar">
            <DerivedMetricsBar metrics={gamma.derived_metrics} />
            <section className="panel exposure-panel" aria-label="Charm y Vanna Exposure">
              <p className="eyebrow">Griegas agregadas</p>
              <div className="exposure-row">
                <div>
                  <span className="exposure-label">Charm Exposure</span>
                  <strong className="exposure-value">
                    {EXPOSURE_FORMAT.format(gamma.charm_exposure)}
                  </strong>
                </div>
                <div>
                  <span className="exposure-label">Vanna Exposure</span>
                  <strong className="exposure-value">
                    {EXPOSURE_FORMAT.format(gamma.vanna_exposure)}
                  </strong>
                </div>
              </div>
            </section>
            <ExpectedMoveWidget key={`expected-move-${symbol}`} expectedMove={market.expected_move} />
            <VolatilitySmile
              key={`volatility-smile-${symbol}`}
              symbol={symbol}
              marketPrice={market.price}
            />
            <QuickScreener />
          </aside>
        </div>
      ) : (
        <section className="panel status" aria-live="polite">
          Cargando régimen y niveles…
        </section>
      )}

      <footer className="tv-footer">
        <AlertsPanel underlyings={underlyings} />
      </footer>
    </main>
  );
}
