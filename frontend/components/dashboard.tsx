"use client";

import Image from "next/image";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Group, Panel, Separator, useDefaultLayout } from "react-resizable-panels";
import { getGamma, getMarket, getUnderlyings } from "@/lib/api";
import {
  aggregateCandles,
  aggregateMinuteCandles,
  type PricePoint,
  type Timeframe,
  type VwapPoint,
} from "@/lib/candles";
import { describeError } from "@/lib/i18n/describe-error";
import { useLanguage, type Language } from "@/lib/i18n/language-context";
import { isWithinRegularSession } from "@/lib/market-session";
import { POLLING_INTERVAL_MS } from "@/lib/polling";
import type { GammaResponse, MarketResponse, Underlying } from "@/lib/types";
import { AlertsPanel } from "./alerts-panel";
import { ChartSecondaryPanel } from "./chart-secondary-panel";
import { ClosingDynamicsPanel } from "./closing-dynamics-panel";
import { DerivedMetricsBar } from "./derived-metrics-bar";
import { EnginesGuidePanel } from "./engines-guide-panel";
import { ExpectedMoveWidget } from "./expected-move-widget";
import { PreSessionPanel } from "./pre-session-panel";
import { PriceChart } from "./price-chart";
import { QuickScreener } from "./quick-screener";
import { VolatilitySmile } from "./volatility-smile";

// Only 1-minute candles are ever fetched (client-side accumulated,
// dashboard-spec.md section 2.2) — the other timeframes are pure
// client-side aggregation of that same data, via aggregateCandles.
const TIMEFRAMES: Timeframe[] = ["1m", "5m", "15m", "1h"];

// Same three columns' fixed pixel widths this layout has always had
// (256px/400px) — now the *default*/min/max for a resizable Group
// instead of a hardcoded CSS flex-basis (dashboard-spec.md section 28).
// Mins are generous enough that neither sidebar can collapse into an
// unusable sliver; the center panel gets its own min so the chart can't
// be squeezed to nothing if both sidebars are dragged toward their max
// at once.
const PANELS_LAYOUT_ID = "convexa-dashboard-panels";
const ALERTS_PANEL_ID = "alerts";
const CENTER_PANEL_ID = "center";
const METRICS_PANEL_ID = "metrics";
const ALERTS_PANEL_MIN_PX = 200;
const ALERTS_PANEL_DEFAULT_PX = 256;
const ALERTS_PANEL_MAX_PX = 480;
const CENTER_PANEL_MIN_PX = 420;
const METRICS_PANEL_MIN_PX = 300;
const METRICS_PANEL_DEFAULT_PX = 400;
const METRICS_PANEL_MAX_PX = 640;

// react-resizable-panels' useDefaultLayout falls back to referencing the
// bare `localStorage` global internally when `storage` is left
// undefined — fine for a pure SPA, but that global doesn't exist during
// Next.js server rendering, so an explicit undefined here still crashed
// SSR ("localStorage is not defined"). A no-op stub sidesteps that
// fallback entirely: reads nothing, writes nothing, never touches the
// real global, so it's safe on the server and harmless on the client's
// pre-hydration render (matches the same "no stored layout yet" case a
// genuinely empty localStorage would produce).
const NOOP_LAYOUT_STORAGE: Pick<Storage, "getItem" | "setItem"> = {
  getItem: () => null,
  setItem: () => {},
};

// Same breakpoint the existing @media (max-width: 960px) rule already
// uses to stack .tv-body into a column on narrow viewports.
const NARROW_LAYOUT_QUERY = "(max-width: 960px)";

function useIsNarrowLayout(): boolean {
  // Defaults to false (desktop/resizable layout) for the server render
  // and the first client render before hydration — same "read real state
  // only after mount" tradeoff already accepted for the language toggle
  // and the panel-size storage above (a possible one-frame flash on a
  // narrow device, never a hydration mismatch). A change listener keeps
  // it in sync afterward: Group's own inline `flex-direction: row`
  // (dashboard-spec.md section 28) can't be overridden by the existing
  // narrow-viewport CSS the way the old plain-flex .tv-body could — a
  // narrow viewport now needs an actually different layout tree, not
  // just different CSS applied to the same one.
  const [isNarrow, setIsNarrow] = useState(false);

  useEffect(() => {
    const query = window.matchMedia(NARROW_LAYOUT_QUERY);
    const update = () => setIsNarrow(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  return isNarrow;
}

const PRICE_FORMAT = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const EXPOSURE_FORMAT = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});

export function Dashboard() {
  const { language, setLanguage, t } = useLanguage();
  // `storage` is undefined during server rendering (and the very first
  // client render, pre-hydration) — `window` doesn't exist there. Same
  // "read the real stored value only after mount" tradeoff already
  // accepted for the language toggle (language-context.tsx): a possible
  // one-frame flash to the default layout if the stored one differs,
  // never a hydration mismatch.
  const { defaultLayout, onLayoutChanged } = useDefaultLayout({
    id: PANELS_LAYOUT_ID,
    storage: typeof window === "undefined" ? NOOP_LAYOUT_STORAGE : window.localStorage,
  });
  const isNarrowLayout = useIsNarrowLayout();
  const [underlyings, setUnderlyings] = useState<Underlying[]>([]);
  const [symbol, setSymbol] = useState("");
  const [view, setView] = useState<"live" | "pre-session">("live");
  const [gamma, setGamma] = useState<GammaResponse | null>(null);
  const [market, setMarket] = useState<MarketResponse | null>(null);
  const [pricePoints, setPricePoints] = useState<PricePoint[]>([]);
  const [vwapPoints, setVwapPoints] = useState<VwapPoint[]>([]);
  // Stores the raw error, not a pre-translated string — translating at
  // render time (via `describeError(error, t)` below) means the message
  // stays correct if the user switches language while it's on screen,
  // instead of being frozen in whichever language was active when the
  // request failed.
  const [error, setError] = useState<unknown>(null);
  const [showEnginesGuide, setShowEnginesGuide] = useState(false);
  const [timeframe, setTimeframe] = useState<Timeframe>("1m");
  const candles = useMemo(() => aggregateMinuteCandles(pricePoints), [pricePoints]);
  const displayedCandles = useMemo(
    () => aggregateCandles(candles, timeframe),
    [candles, timeframe],
  );
  const latestCandle = displayedCandles.at(-1) ?? null;

  useEffect(() => {
    const controller = new AbortController();
    getUnderlyings(controller.signal)
      .then(({ underlyings: items }) => {
        setUnderlyings(items);
        setSymbol((current) => current || items[0]?.symbol || "");
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason);
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
      // The chart is meant to show only the regular 09:30-16:00 ET
      // session -- confirmed live, 2026-09: the backend stream gate
      // (StreamUnderlyingPriceUseCase) stops *new* extended-hours ticks
      // from being stored, but a tick written before that gate existed
      // can still be the "latest" MarketPrice this polls until the next
      // in-session write, so this stays defensive here too rather than
      // trusting the API response's as_of unconditionally.
      if (isWithinRegularSession(Date.parse(marketData.as_of))) {
        setPricePoints((current) => [
          ...current,
          { timestamp: marketData.as_of, price: marketData.price },
        ]);
      }
      const anchoredVwap = marketData.anchored_vwap;
      // Same session gate as pricePoints above -- VWAP renders on a Line
      // series on the *same* chart/timeScale as the candlesticks, so an
      // out-of-session point here would drag the shared x-axis just as
      // badly as an out-of-session candle would.
      if (
        anchoredVwap &&
        !anchoredVwap.provisional &&
        anchoredVwap.value !== null &&
        isWithinRegularSession(Date.parse(marketData.as_of))
      ) {
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
      setError(null);
    } catch (reason: unknown) {
      if (!signal?.aborted) {
        setError(reason);
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
            <span className="sr-only">{t.dashboard.underlyingLabel}</span>
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
          <div
            className="tv-language-toggle"
            role="group"
            aria-label={t.common.languageSwitcherAriaLabel}
          >
            {(["es", "en"] as Language[]).map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={language === option}
                onClick={() => setLanguage(option)}
              >
                {option.toUpperCase()}
              </button>
            ))}
          </div>
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
        <div className="tv-timeframes" role="group" aria-label={t.dashboard.timeframeGroupAriaLabel}>
          {TIMEFRAMES.map((option) => (
            <button
              key={option}
              type="button"
              className="tv-timeframe"
              aria-pressed={timeframe === option}
              onClick={() => setTimeframe(option)}
            >
              {option}
            </button>
          ))}
        </div>
        <div className="tv-view-toggle" role="group" aria-label={t.dashboard.viewGroupAriaLabel}>
          <button
            type="button"
            aria-pressed={view === "live"}
            onClick={() => setView("live")}
          >
            {t.dashboard.liveButton}
          </button>
          <button
            type="button"
            aria-pressed={view === "pre-session"}
            onClick={() => setView("pre-session")}
          >
            {t.dashboard.preSessionButton}
          </button>
        </div>
        <div className="tv-topbar-right">
          <button
            type="button"
            className="tv-settings-button"
            aria-label={t.enginesGuide.triggerAriaLabel}
            onClick={() => setShowEnginesGuide(true)}
          >
            ☰
          </button>
        </div>
      </header>

      {showEnginesGuide && (
        <EnginesGuidePanel onClose={() => setShowEnginesGuide(false)} />
      )}

      {error ? (
        <section className="panel status error" role="alert">
          {describeError(error, t)}
        </section>
      ) : gamma && market ? (
        (() => {
          const alertsContent = (
            <aside className="tv-alerts-sidebar">
              <AlertsPanel symbol={symbol} orientation="vertical" />
            </aside>
          );
          const centerContent = (
            <div className="tv-center">
              {view === "live" ? (
                <>
                  <PriceChart
                    key={`price-chart-${symbol}-${timeframe}`}
                    symbol={symbol}
                    candles={displayedCandles}
                    gamma={gamma}
                    vwapPoints={vwapPoints}
                    atrRange={market.atr_range}
                    timeframe={timeframe}
                  />
                  <ChartSecondaryPanel
                    key={`chart-secondary-${symbol}`}
                    symbol={symbol}
                    spotPrice={market.price}
                  />
                </>
              ) : (
                <PreSessionPanel
                  key={`pre-session-${symbol}`}
                  symbol={symbol}
                  gamma={gamma}
                  market={market}
                />
              )}
            </div>
          );
          const metricsContent = (
            <aside className="tv-sidebar">
              <DerivedMetricsBar metrics={gamma.derived_metrics} />
              <section
                className="panel exposure-panel"
                aria-label={t.dashboard.exposureGroupAriaLabel}
              >
                <p className="eyebrow">{t.dashboard.aggregatedGreeksEyebrow}</p>
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
              {/* Right after the raw Charm/Vanna Exposure numbers above, since
                  this panel is their translated, closing-window-scoped
                  interpretation — conditional by design (dashboard-spec.md
                  section 9), not a toggle, so it renders nothing outside the
                  closing window. */}
              <ClosingDynamicsPanel closingDynamics={market.closing_dynamics} />
              <ExpectedMoveWidget key={`expected-move-${symbol}`} expectedMove={market.expected_move} />
              <VolatilitySmile
                key={`volatility-smile-${symbol}`}
                symbol={symbol}
                marketPrice={market.price}
              />
              <QuickScreener />
            </aside>
          );

          // Narrow viewports keep the original plain-flex stacked layout
          // (unchanged from before react-resizable-panels) rather than a
          // resizable one — Group's own inline flex-direction can't be
          // overridden by the existing @media stacking rule, and dragging
          // to resize isn't a meaningful gesture on a touch-sized screen
          // anyway. See useIsNarrowLayout above.
          if (isNarrowLayout) {
            return (
              <div className="tv-body">
                {alertsContent}
                {centerContent}
                {metricsContent}
              </div>
            );
          }

          return (
            <Group
              className="tv-body"
              orientation="horizontal"
              id={PANELS_LAYOUT_ID}
              defaultLayout={defaultLayout}
              onLayoutChanged={onLayoutChanged}
            >
              <Panel
                id={ALERTS_PANEL_ID}
                defaultSize={ALERTS_PANEL_DEFAULT_PX}
                minSize={ALERTS_PANEL_MIN_PX}
                maxSize={ALERTS_PANEL_MAX_PX}
              >
                {alertsContent}
              </Panel>

              <Separator
                className="tv-resize-separator"
                aria-label={t.dashboard.resizeSeparatorAriaLabel}
              />

              <Panel id={CENTER_PANEL_ID} minSize={CENTER_PANEL_MIN_PX}>
                {centerContent}
              </Panel>

              <Separator
                className="tv-resize-separator"
                aria-label={t.dashboard.resizeSeparatorAriaLabel}
              />

              <Panel
                id={METRICS_PANEL_ID}
                defaultSize={METRICS_PANEL_DEFAULT_PX}
                minSize={METRICS_PANEL_MIN_PX}
                maxSize={METRICS_PANEL_MAX_PX}
              >
                {metricsContent}
              </Panel>
            </Group>
          );
        })()
      ) : (
        <section className="panel status" aria-live="polite">
          {t.dashboard.loadingRegime}
        </section>
      )}
    </main>
  );
}
