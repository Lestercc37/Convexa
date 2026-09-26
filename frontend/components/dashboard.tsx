"use client";

import Image from "next/image";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Group, Panel, Separator, useDefaultLayout } from "react-resizable-panels";
import {
  getGamma,
  getMarket,
  getMarketPriceHistory,
  getUnderlyings,
  getVwapHistory,
  logout,
} from "@/lib/api";
import {
  aggregateCandles,
  aggregateMinuteCandles,
  type PricePoint,
  type Timeframe,
  type VwapPoint,
} from "@/lib/candles";
import { describeError } from "@/lib/i18n/describe-error";
import { useLanguage, type Language } from "@/lib/i18n/language-context";
import { isWithinTheMostRecentSession } from "@/lib/market-session";
import {
  connectMarketPriceStream,
  type MarketPriceStreamStatus,
} from "@/lib/market-price-stream";
import { POLLING_INTERVAL_MS } from "@/lib/polling";
import type { GammaResponse, GammaView, MarketResponse, Underlying } from "@/lib/types";
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

// Confirmed with the user, 2026-09-26: the dashboard always opening on
// AAPL (the first symbol alphabetically in the underlyings list) on
// every fresh load/refresh was disorienting for a team that each
// mostly watches one or two symbols. Remembers the last symbol chosen,
// per browser (localStorage, not a server-side per-user preference) --
// good enough for now since each teammate always uses their own
// machine/browser profile.
const LAST_SYMBOL_STORAGE_KEY = "convexa:last-symbol";

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
  const router = useRouter();
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
  const [view, setView] = useState<"live" | "pre-session" | "scanner">("live");
  // Structural vs. Tactical (0-2 DTE) -- named gammaView, not view, since
  // that name is already the Live/Pre-Session/Scanner tab selector above.
  const [gammaView, setGammaView] = useState<GammaView>("structural");
  const [gamma, setGamma] = useState<GammaResponse | null>(null);
  const [market, setMarket] = useState<MarketResponse | null>(null);
  const [pricePoints, setPricePoints] = useState<PricePoint[]>([]);
  const [vwapPoints, setVwapPoints] = useState<VwapPoint[]>([]);
  const [vwapNotApplicable, setVwapNotApplicable] = useState(false);
  const [vwapProxySymbol, setVwapProxySymbol] = useState<string | null>(null);
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
  // Structural now refreshes on its own slower cadence on the backend
  // (STRUCTURAL_REFRESH_INTERVAL, gamma.py) -- a scalper/day trader's
  // own request (2026-09-25): Tactical is the working set and needs
  // every cycle's freshness, Structural is background macro context
  // that doesn't. This surfaces gamma.as_of next to the toggle so an
  // unmoving Call Wall/Put Wall under Structural reads as "intentionally
  // slower," not "stuck." Only shown for Structural -- Tactical is
  // always as fresh as the latest poll, same as every other panel.
  const structuralMinutesAgo =
    gamma && gammaView === "structural"
      ? Math.max(0, Math.floor((Date.now() - Date.parse(gamma.as_of)) / 60_000))
      : null;

  useEffect(() => {
    const controller = new AbortController();
    getUnderlyings(controller.signal)
      .then(({ underlyings: items }) => {
        setUnderlyings(items);
        setSymbol((current) => {
          if (current) return current;
          const stored = window.localStorage.getItem(LAST_SYMBOL_STORAGE_KEY);
          const isStoredStillActive = stored && items.some((item) => item.symbol === stored);
          return (isStoredStillActive ? stored : items[0]?.symbol) || "";
        });
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason);
        }
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!symbol) return;
    window.localStorage.setItem(LAST_SYMBOL_STORAGE_KEY, symbol);
  }, [symbol]);

  // `view` is an explicit parameter, not read off the `gammaView` state
  // closure -- this is what lets refresh() stay referentially stable
  // ([] deps, matching this callback's original shape pre-dating the
  // gammaView feature) instead of getting a new identity every time
  // gammaView changes. A new identity here would have forced the
  // seed-then-poll effect below (keyed on `[refresh, symbol]`) to tear
  // down and restart from scratch on every view toggle -- re-seeding
  // the whole day's price history AND VWAP history, not just re-fetching
  // gamma, which is what actually made toggling the view feel like a
  // full symbol switch (confirmed live, 2026-09-25, right after this
  // feature shipped).
  const refresh = useCallback(async (activeSymbol: string, view: GammaView, signal?: AbortSignal) => {
    if (!activeSymbol) return;
    try {
      const [gammaData, marketData] = await Promise.all([
        getGamma(activeSymbol, view, signal),
        getMarket(activeSymbol, signal),
      ]);
      setGamma(gammaData);
      setMarket(marketData);
      // The chart is meant to show only the most recent regular
      // 09:30-16:00 ET session -- confirmed live, 2026-09: the backend
      // stream gate (StreamUnderlyingPriceUseCase) stops *new*
      // extended-hours ticks from being stored, but a tick written
      // before that gate existed can still be the "latest" MarketPrice
      // this polls until the next in-session write, so this stays
      // defensive here too rather than trusting the API response's
      // as_of unconditionally. isWithinTheMostRecentSession, not
      // isWithinRegularSession -- a stale price from a real, but
      // *older*, trading day used to pass the weaker check (it only
      // verified the timestamp was within *some* session, its own) and
      // get plotted as if it were current, corrupting the x-axis
      // against PriceChart's own anchor (see that check's own comment).
      if (isWithinTheMostRecentSession(Date.parse(marketData.as_of))) {
        setPricePoints((current) => [
          ...current,
          { timestamp: marketData.as_of, price: marketData.price },
        ]);
      }
      const anchoredVwap = marketData.anchored_vwap;
      setVwapProxySymbol(anchoredVwap?.proxy_symbol ?? null);
      // Same session gate as pricePoints above -- VWAP renders on a Line
      // series on the *same* chart/timeScale as the candlesticks, so an
      // out-of-session point here would drag the shared x-axis just as
      // badly as an out-of-session candle would.
      if (
        anchoredVwap &&
        !anchoredVwap.provisional &&
        anchoredVwap.value !== null &&
        isWithinTheMostRecentSession(Date.parse(marketData.as_of))
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

  // Lets the poll/interval below always read the *current* gammaView at
  // call time without needing it in their own dependency arrays (which
  // would re-trigger the full reseed effect on every toggle -- see
  // refresh's own comment above). Deliberately a ref, not the gammaView
  // state itself: this file never reads gammaViewRef.current during
  // render, only inside the async callbacks below.
  const gammaViewRef = useRef(gammaView);
  useEffect(() => {
    gammaViewRef.current = gammaView;
  }, [gammaView]);

  useEffect(() => {
    if (!symbol) return;
    const controller = new AbortController();
    let interval: number | undefined;

    // Seeds pricePoints (and vwapPoints, same reasoning -- Lester's
    // report, 2026-09-17) with today's session-so-far before the first
    // live poll runs, instead of starting every symbol from an empty
    // chart that has to wait for new ticks to rebuild candles already
    // formed since the open (confirmed live, 2026-09: the backend
    // already had this data via market_snapshots -- GET
    // /market/{symbol}/history and /vwap-history expose it, this just
    // seeds with it). A failed seed isn't fatal -- it just falls back to
    // the old empty-then-accumulate behavior for that one series.
    //
    // Promise.allSettled, not two sequential awaits (confirmed live,
    // 2026-09-26: AAPL's own Friday session alone is 12,000+ raw price
    // points -- fetching + parsing that, THEN starting the VWAP fetch
    // only after it finished, was most of a real ~10s symbol-switch
    // delay teammates reported). Settled, not Promise.all, so one
    // endpoint failing still lets the other's real data apply instead of
    // discarding it too.
    const seedThenPoll = async () => {
      const [historyResult, vwapResult] = await Promise.allSettled([
        getMarketPriceHistory(symbol, controller.signal),
        getVwapHistory(symbol, controller.signal),
      ]);
      if (controller.signal.aborted) return;

      if (historyResult.status === "fulfilled") {
        setPricePoints(
          historyResult.value.points.map((point) => ({
            timestamp: point.timestamp,
            price: point.price,
          })),
        );
      } else if (!controller.signal.aborted) {
        setError(historyResult.reason);
      }

      if (vwapResult.status === "fulfilled") {
        setVwapNotApplicable(vwapResult.value.not_applicable);
        setVwapPoints(
          vwapResult.value.points.map((point) => ({
            timestamp: point.timestamp,
            value: point.value,
          })),
        );
      } else if (!controller.signal.aborted) {
        setError(vwapResult.reason);
      }

      if (controller.signal.aborted) return;
      void refresh(symbol, gammaViewRef.current, controller.signal);
      interval = window.setInterval(
        () => void refresh(symbol, gammaViewRef.current),
        POLLING_INTERVAL_MS,
      );
    };
    void seedThenPoll();

    return () => {
      controller.abort();
      if (interval !== undefined) window.clearInterval(interval);
    };
  }, [refresh, symbol]);

  // Real-time push, additive to the 30s poll above -- never a
  // replacement for it (see market-price-stream.ts's own comment). A
  // tick appends to pricePoints exactly like the poll's own append
  // does, so it flows through the same aggregateMinuteCandles ->
  // PriceChart pipeline and updates the in-progress candle immediately
  // instead of waiting for the next 30s cycle. Starts as "fallback",
  // not "connected" -- there's no live tick yet at this point, and the
  // stream itself now reconnects with backoff instead of going quiet
  // forever on the first drop (market-price-stream.ts's own comment).
  // Not reset synchronously on symbol change: connectMarketPriceStream's
  // onStatusChange always fires from the new WebSocket's own async
  // onopen/onclose, never synchronously within this effect, so the
  // previous symbol's status is naturally overwritten the moment the
  // new connection attempt resolves either way.
  const [streamStatus, setStreamStatus] = useState<MarketPriceStreamStatus>("fallback");
  useEffect(() => {
    if (!symbol) return;
    const disconnect = connectMarketPriceStream(
      symbol,
      (tick) => {
        if (!isWithinTheMostRecentSession(Date.parse(tick.as_of))) return;
        setPricePoints((current) => [
          ...current,
          { timestamp: tick.as_of, price: Number(tick.price) },
        ]);
      },
      setStreamStatus,
    );
    return disconnect;
  }, [symbol]);

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
                setVwapNotApplicable(false);
                setVwapProxySymbol(null);
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
          <button
            type="button"
            aria-pressed={view === "scanner"}
            onClick={() => setView("scanner")}
          >
            {t.dashboard.scannerButton}
          </button>
        </div>
        <div
          className="tv-gamma-view-toggle"
          role="group"
          aria-label={t.dashboard.gammaViewGroupAriaLabel}
        >
          <button
            type="button"
            aria-pressed={gammaView === "structural"}
            onClick={() => {
              setGamma(null);
              setGammaView("structural");
              // Targeted, immediate re-fetch -- not a symbol switch, so
              // it deliberately does NOT touch pricePoints/vwapPoints or
              // re-seed their own history the way an actual symbol
              // change does (see the seed-then-poll effect above). This
              // is what makes toggling the view fast instead of feeling
              // like a full symbol reload.
              void refresh(symbol, "structural");
            }}
          >
            {t.dashboard.structuralButton}
          </button>
          <button
            type="button"
            aria-pressed={gammaView === "tactical"}
            onClick={() => {
              setGamma(null);
              setGammaView("tactical");
              void refresh(symbol, "tactical");
            }}
          >
            {t.dashboard.tacticalButton}
          </button>
        </div>
        {structuralMinutesAgo !== null && (
          <span className="tv-structural-as-of" role="status">
            {t.dashboard.structuralAsOfLabel(structuralMinutesAgo)}
          </span>
        )}
        <div className="tv-topbar-right">
          <button
            type="button"
            className="tv-settings-button"
            aria-label={t.enginesGuide.triggerAriaLabel}
            onClick={() => setShowEnginesGuide(true)}
          >
            ☰
          </button>
          <button
            type="button"
            className="tv-logout-button"
            onClick={() => {
              void logout().finally(() => router.replace("/login"));
            }}
          >
            {t.dashboard.logoutButton}
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
                    gammaView={gammaView}
                    vwapPoints={vwapPoints}
                    vwapNotApplicable={vwapNotApplicable}
                    vwapProxySymbol={vwapProxySymbol}
                    atrRange={market.atr_range}
                    expectedMove={market.expected_move}
                    timeframe={timeframe}
                    streamStatus={streamStatus}
                  />
                  <ChartSecondaryPanel
                    key={`chart-secondary-${symbol}`}
                    symbol={symbol}
                    spotPrice={market.price}
                    gamma={gamma}
                    gammaView={gammaView}
                  />
                </>
              ) : view === "pre-session" ? (
                <PreSessionPanel
                  key={`pre-session-${symbol}-${gammaView}`}
                  symbol={symbol}
                  gamma={gamma}
                  gammaView={gammaView}
                />
              ) : (
                <QuickScreener />
              )}
            </div>
          );
          const metricsContent = (
            <aside className="tv-sidebar">
              <DerivedMetricsBar
                metrics={gamma.derived_metrics}
                showStructuralOnlyBadge={gammaView === "tactical"}
              />
              <section
                className="panel exposure-panel"
                aria-label={t.dashboard.exposureGroupAriaLabel}
              >
                <p className="eyebrow">{t.dashboard.aggregatedGreeksEyebrow}</p>
                <div className="exposure-row">
                  <div>
                    <span className="exposure-label">Delta Exposure</span>
                    <strong className="exposure-value">
                      {EXPOSURE_FORMAT.format(gamma.delta_exposure)}
                    </strong>
                  </div>
                  <div>
                    <span className="exposure-label">Vega Exposure</span>
                    <strong className="exposure-value">
                      {EXPOSURE_FORMAT.format(gamma.vega_exposure)}
                    </strong>
                  </div>
                </div>
                <div className="exposure-row">
                  <div>
                    <span className="exposure-label">Theta Exposure</span>
                    <strong className="exposure-value">
                      {EXPOSURE_FORMAT.format(gamma.theta_exposure)}
                    </strong>
                  </div>
                  <div>
                    <span className="exposure-label">Charm Exposure</span>
                    <strong className="exposure-value">
                      {EXPOSURE_FORMAT.format(gamma.charm_exposure)}
                    </strong>
                  </div>
                </div>
                <div className="exposure-row">
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
              <ClosingDynamicsPanel closingDynamics={market.closing_dynamics} spotPrice={market.price} />
              <ExpectedMoveWidget key={`expected-move-${symbol}`} expectedMove={market.expected_move} />
              <VolatilitySmile
                key={`volatility-smile-${symbol}`}
                symbol={symbol}
                marketPrice={market.price}
              />
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
