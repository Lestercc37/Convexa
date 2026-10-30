"use client";

import Image from "next/image";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Group, Panel, Separator, useDefaultLayout } from "react-resizable-panels";
import {
  ApiError,
  getGamma,
  getMarket,
  getMarketPriceHistory,
  getOptionChainExpirations,
  getPriceCandles,
  getUnderlyings,
  getVwapHistory,
  logout,
} from "@/lib/api";
import {
  aggregateCandles,
  aggregateMinuteCandles,
  type MinuteCandle,
  type PricePoint,
  type Timeframe,
  type VwapPoint,
} from "@/lib/candles";
import { startChartLifecycleRecorder } from "@/lib/chart-lifecycle";
import {
  DEFAULT_GAMMA_VIEW,
  effectiveGammaView,
  type GammaWeight,
  readStoredGammaWeight,
  storeGammaWeight,
  supportsVolumeWeight,
  GAMMA_SOURCE_SYMBOL_BY_FUTURE,
  GAMMA_VIEW_TABS,
  isExpirationView,
  listedExpirationForView,
  readStoredGammaView,
  storeGammaView,
  todayInNewYork,
} from "@/lib/gamma-views";
import { describeError } from "@/lib/i18n/describe-error";
import { useLanguage, type Language } from "@/lib/i18n/language-context";
import { RegimeCompactBadge } from "./regime-badge";
import { ExpectedMoveCompactBadge } from "./expected-move-badge";
import { SessionClock } from "./session-clock";
import { isWithinTheMostRecentSession } from "@/lib/market-session";
import {
  connectMarketPriceStream,
  type MarketPriceStreamStatus,
} from "@/lib/market-price-stream";
import { MARKET_POLLING_INTERVAL_MS, POLLING_INTERVAL_MS } from "@/lib/polling";
import type { GammaResponse, GammaView, MarketResponse, Underlying } from "@/lib/types";
import { AlertsPanel } from "./alerts-panel";
import { DealersPanel } from "./derived-metrics-bar";
import { EnginesGuidePanel } from "./engines-guide-panel";
import { FutureOpeningPriceControl } from "./future-opening-price-control";
import { PreSessionPanel } from "./pre-session-panel";
import { ChartErrorBoundary } from "./chart-error-boundary";
import { CHART_LAYERS, chartRouteQuery, layerPreset, type ChartRouteState } from "@/lib/view-route";
import { V2Dropdown } from "./v2-dropdown";
import { PriceChart } from "./price-chart";
import { QuickScreener } from "./quick-screener";

// Only 1-minute candles are ever fetched (client-side accumulated,
// dashboard-spec.md section 2.2) — the other timeframes are pure
// client-side aggregation of that same data, via aggregateCandles.
const TIMEFRAMES: Timeframe[] = ["1m", "5m", "15m", "1h"];
// The Semana range has no 1-minute candles (a week of them is ~2,000): its timeframes start at 5m.
const WEEK_TIMEFRAMES: Timeframe[] = ["5m", "15m", "1h"];
const WEEK_SESSIONS = 5;
const EMPTY_CANDLES: MinuteCandle[] = [];

// The alerts column's pixel width this layout has always had (256px) — the
// *default*/min/max for a resizable Group instead of a hardcoded CSS
// flex-basis (dashboard-spec.md section 28). The right-hand metrics column
// is gone (approved mockup, 2026-10-18): the chart takes the whole width and
// the Dealers group sits under it. The min keeps the alerts column from
// collapsing into an unusable sliver; the center panel gets its own min so
// the chart can't be squeezed to nothing.
const PANELS_LAYOUT_ID = "convexa-dashboard-panels-v2";
const ALERTS_PANEL_ID = "alerts";
const CENTER_PANEL_ID = "center";
const ALERTS_PANEL_MIN_PX = 200;
const ALERTS_PANEL_DEFAULT_PX = 256;
const ALERTS_PANEL_MAX_PX = 480;
const CENTER_PANEL_MIN_PX = 420;

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
// v2 "Reducir movimiento" (desktop app windows).
const REDUCE_MOTION_STORAGE_KEY = "convexa.reduceMotion";

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

type DashboardProps = {
  // "chart" = the /vista/grafico route (desktop app windows): the chart alone, no alerts column, no dealers cards, Live only. "full" = the web dashboard as it is.
  mode?: "full" | "chart";
  // The address of a /vista/grafico window: its starting symbol / timeframe / range / tab and its one layer (none = plain chart).
  route?: ChartRouteState;
  // Called when the viewer changes the symbol, timeframe, range or tab, so the route can keep its address current (the app opens layer windows from it).
  onRouteChange?: (state: ChartRouteState) => void;
};

export function Dashboard({ mode = "full", route, onRouteChange }: DashboardProps = {}) {
  const isChartOnly = mode === "chart";
  // The address the window opened with; only its starting values are used (later changes go the other way, through onRouteChange).
  const [initialRoute] = useState(route);
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
  // The expiration tabs 0DTE | 1DTE | 2DTE | 0-2 DTE (Lester 2026-10-26; the Structural view was retired 2026-10-16). Opens on 0DTE, then
  // remembers the last tab in this browser. Named gammaView, not view, since that name is already the Live/Pre-Session/Scanner selector above.
  const [gammaTab, setGammaTab] = useState<GammaView>(DEFAULT_GAMMA_VIEW);
  // "OI | Volumen": weight the 0DTE tab by the open interest (default) or by the contracts traded today (GEXBot's "GEX (Volume)"). 0DTE only for now.
  const [gammaWeight, setGammaWeight] = useState<GammaWeight>("oi");
  // What every request asks for: the tab, or its volume-weighted twin.
  const gammaView = effectiveGammaView(gammaTab, gammaWeight);
  // The listed expirations of the symbol whose levels are shown (SPX / NDX for ES / NQ), for the "vence ..." label next to the tabs.
  const [expirations, setExpirations] = useState<{ symbol: string; dates: string[] }>({ symbol: "", dates: [] });
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
  const [timeframe, setTimeframe] = useState<Timeframe>(route?.timeframe ?? "1m");
  // Day (default) or Semana: the last 5 trading dates as server-aggregated candles. Day mode is untouched by it.
  const [chartRange, setChartRange] = useState<"day" | "week">(route?.range ?? "day");
  // Keyed by symbol and timeframe so a response (or a leftover) for another one is never shown.
  const [weekData, setWeekData] = useState<{ key: string; candles: MinuteCandle[] }>({ key: "", candles: [] });
  const candles = useMemo(() => aggregateMinuteCandles(pricePoints), [pricePoints]);
  const dayCandles = useMemo(
    () => aggregateCandles(candles, timeframe),
    [candles, timeframe],
  );
  const isFutureSymbol = underlyings.find((item) => item.symbol === symbol)?.kind === "future";
  // ES/NQ have no stored prices of their own (the index shifted by today's opening price): Semana does not exist for them.
  const isWeek = chartRange === "week" && !isFutureSymbol;
  const shownTimeframe: Timeframe = isWeek && timeframe === "1m" ? "5m" : timeframe;
  const weekKey = `${symbol}|${shownTimeframe}`;
  const weekCandles = weekData.key === weekKey ? weekData.candles : EMPTY_CANDLES;
  const displayedCandles = isWeek ? weekCandles : dayCandles;
  const latestCandle = displayedCandles.at(-1) ?? null;
  // ES/NQ are SPX/NDX shifted by the opening price typed in by hand each morning. Without today's
  // price the API answers 409: say "no data" (never stale numbers, never hide the symbol).
  const hasNoOpeningPrice = isFutureSymbol && error instanceof ApiError && error.status === 409;

  // Local-only flight recorder for page loads / tab visibility / network (see lib/chart-lifecycle.ts).
  useEffect(() => startChartLifecycleRecorder(), []);

  useEffect(() => {
    const controller = new AbortController();
    getUnderlyings(controller.signal)
      .then(({ underlyings: items }) => {
        setUnderlyings(items);
        setSymbol((current) => {
          if (current) return current;
          const routeSymbol = initialRoute?.symbol;
          if (routeSymbol && items.some((item) => item.symbol === routeSymbol)) return routeSymbol;
          const stored = window.localStorage.getItem(LAST_SYMBOL_STORAGE_KEY);
          const isStoredStillActive = stored && items.some((item) => item.symbol === stored);
          // A fresh load opens on SPX (the first symbol alphabetically is now ES, a
          // price-proxy future with no options chain of its own), else the first
          // non-future symbol.
          const fallback =
            items.find((item) => item.symbol === "SPX") ??
            items.find((item) => item.kind !== "future") ??
            items[0];
          return (isStoredStillActive ? stored : fallback?.symbol) || "";
        });
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason);
        }
      });
    return () => controller.abort();
  }, [initialRoute]);

  useEffect(() => {
    if (!symbol) return;
    window.localStorage.setItem(LAST_SYMBOL_STORAGE_KEY, symbol);
  }, [symbol]);

  // Keep a /vista/grafico window's address in step with what it shows (the desktop app reads it to open layer windows on the same symbol).
  useEffect(() => {
    if (!onRouteChange || !symbol) return;
    onRouteChange({ symbol, timeframe, range: chartRange, gammaTab, layer: initialRoute?.layer ?? null });
  }, [onRouteChange, symbol, timeframe, chartRange, gammaTab, initialRoute]);

  useEffect(() => {
    const stored = initialRoute?.gammaTab ?? readStoredGammaView();
    if (stored) setGammaTab(stored);
    setGammaWeight(readStoredGammaWeight());
  }, [initialRoute]);

  const gammaSourceSymbol = GAMMA_SOURCE_SYMBOL_BY_FUTURE[symbol] ?? symbol;
  useEffect(() => {
    if (!gammaSourceSymbol) return;
    const controller = new AbortController();
    const load = () =>
      getOptionChainExpirations(gammaSourceSymbol, controller.signal)
        .then((response) => {
          if (!controller.signal.aborted) setExpirations({ symbol: gammaSourceSymbol, dates: response.expirations });
        })
        .catch(() => {
          // The label is only a convenience: without it the tabs still work.
        });
    void load();
    // Re-read every 10 minutes so the label rolls to the next expiration after midnight without a reload.
    const interval = window.setInterval(() => void load(), 10 * 60_000);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [gammaSourceSymbol]);

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
  // The market snapshot's price point and anchored VWAP point, shared by the full refresh (every 30 s, with gamma) and the market-only poll (every
  // 10 s, Lester 2026-10-28: the VWAP line moved only every 30 s; /market is cached server-side for 3 s, so the extra polls are cheap).
  const applyMarket = useCallback((marketData: MarketResponse) => {
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
  }, []);

  const refresh = useCallback(async (activeSymbol: string, view: GammaView, signal?: AbortSignal) => {
    if (!activeSymbol) return;
    try {
      const [gammaData, marketData] = await Promise.all([
        getGamma(activeSymbol, view, signal),
        getMarket(activeSymbol, signal),
      ]);
      // A poll that left before a tab switch must not put the previous tab's levels back on screen.
      if (view === gammaViewRef.current) setGamma(gammaData);
      applyMarket(marketData);
      setError(null);
    } catch (reason: unknown) {
      if (!signal?.aborted) {
        setError(reason);
      }
    }
  }, [applyMarket]);

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

  const applyGammaView = (tab: GammaView, weight: GammaWeight) => {
    const next = effectiveGammaView(tab, weight);
    setGammaTab(tab);
    setGammaWeight(weight);
    if (next === gammaView) return;
    gammaViewRef.current = next;
    if (symbol) void refresh(symbol, next);
  };
  const selectGammaView = (tab: GammaView) => {
    storeGammaView(tab);
    applyGammaView(tab, gammaWeight);
  };
  const selectGammaWeight = (weight: GammaWeight) => {
    storeGammaWeight(weight);
    applyGammaView(gammaTab, weight);
  };
  const today = todayInNewYork();
  const shownExpiration =
    expirations.symbol === gammaSourceSymbol ? listedExpirationForView(expirations.dates, gammaTab, today) : null;
  const expirationLabel = (() => {
    if (!isExpirationView(gammaTab) || expirations.symbol !== gammaSourceSymbol) return null;
    if (!shownExpiration) return t.dashboard.gammaViewNoExpirationLabel;
    const date = new Intl.DateTimeFormat(language === "es" ? "es-ES" : "en-US", {
      weekday: "short",
      day: "2-digit",
      month: "2-digit",
      timeZone: "UTC",
    }).format(new Date(`${shownExpiration}T00:00:00Z`));
    return `${t.dashboard.gammaViewExpiresLabel} ${date}`;
  })();

  // Seeds pricePoints (and vwapPoints, same reasoning -- Lester's
  // report, 2026-09-17) with today's session-so-far, instead of starting
  // every symbol from an empty chart that has to wait for new ticks to
  // rebuild candles already formed since the open (confirmed live,
  // 2026-09: the backend already had this data via market_snapshots --
  // GET /market/{symbol}/history and /vwap-history expose it, this just
  // seeds with it). A failed seed isn't fatal -- it just falls back to
  // the old empty-then-accumulate behavior for that one series.
  //
  // Extracted from the symbol-switch effect below so
  // FutureOpeningPriceControl (ES/NQ) can also call it right after
  // saving a new opening-price anchor, to repaint the chart/VWAP with
  // the newly-calibrated proxy series immediately instead of waiting for
  // the next 30s poll.
  //
  // Promise.allSettled, not two sequential awaits (confirmed live,
  // 2026-09-26: AAPL's own Friday session alone is 12,000+ raw price
  // points -- fetching + parsing that, THEN starting the VWAP fetch
  // only after it finished, was most of a real ~10s symbol-switch
  // delay teammates reported). Settled, not Promise.all, so one
  // endpoint failing still lets the other's real data apply instead of
  // discarding it too.
  const seedPriceAndVwap = useCallback(async (activeSymbol: string, signal?: AbortSignal) => {
    const [historyResult, vwapResult] = await Promise.allSettled([
      getMarketPriceHistory(activeSymbol, signal),
      getVwapHistory(activeSymbol, signal),
    ]);
    if (signal?.aborted) return;

    if (historyResult.status === "fulfilled") {
      const seeded = historyResult.value.points.map((point) => ({
        timestamp: point.timestamp,
        price: point.price,
      }));
      // A merge, not a replace -- this fetch runs concurrently with the
      // WS tick effect below, which can append a live tick (via its own
      // functional setPricePoints update) before this slower network
      // round-trip resolves. Replacing outright here used to silently
      // discard that tick, and also fooled PriceChart's own
      // zero-to-nonzero transition guard (previousCandleCount) into
      // thinking this was a steady-state single-bar update rather than
      // a reseed, routing it through series.update() against internal
      // chart state that no longer matched `candles` -- the exact
      // precondition PriceChart's own comment documents for its
      // uncaught "Value is null" crash. Confirmed live, 2026-10-01,
      // right at market open, when the first live ticks are most likely
      // to race ahead of this fetch. Keeping anything already in
      // `current` that's newer than the seed's own last point preserves
      // those ticks and keeps this a strict superset, so the chart only
      // ever grows forward, never swaps out from under itself.
      setPricePoints((current) => {
        const lastSeededTimestamp = seeded.at(-1)?.timestamp;
        const newerThanSeed = lastSeededTimestamp
          ? current.filter(
              (point) => Date.parse(point.timestamp) > Date.parse(lastSeededTimestamp),
            )
          : current;
        return [...seeded, ...newerThanSeed];
      });
    } else if (!signal?.aborted) {
      setError(historyResult.reason);
    }

    if (vwapResult.status === "fulfilled") {
      setVwapNotApplicable(vwapResult.value.not_applicable);
      const seeded = vwapResult.value.points.map((point) => ({
        timestamp: point.timestamp,
        value: point.value,
      }));
      // Same race as pricePoints above -- the refresh() poll's own VWAP
      // append is a functional update too and can land first.
      setVwapPoints((current) => {
        const lastSeededTimestamp = seeded.at(-1)?.timestamp;
        const newerThanSeed = lastSeededTimestamp
          ? current.filter(
              (point) => Date.parse(point.timestamp) > Date.parse(lastSeededTimestamp),
            )
          : current;
        return [...seeded, ...newerThanSeed];
      });
    } else if (!signal?.aborted) {
      setError(vwapResult.reason);
    }
  }, []);

  useEffect(() => {
    if (!symbol) return;
    const controller = new AbortController();
    let interval: number | undefined;
    let marketInterval: number | undefined;

    const seedThenPoll = async () => {
      await seedPriceAndVwap(symbol, controller.signal);
      if (controller.signal.aborted) return;
      void refresh(symbol, gammaViewRef.current, controller.signal);
      // controller.signal here too, not just the call above -- confirmed
      // live, 2026-10-01: without it, a poll tick that was already in
      // flight when the user switched symbols has nothing aborting it,
      // so its response (the *old* symbol's gamma) can land after the
      // switch and land in setGamma() for the *new* symbol. gamma feeds
      // PriceChart's own autoscale (referenceLevelPrices/mergePriceRange
      // merge every gamma level into the visible price range), so one
      // stale cross-symbol Call Wall/Put Wall was enough to stretch the
      // Y-axis to span both symbols' price scales at once -- the
      // "velas comprimidas" symptom reported this session on an NDX
      // switch. refresh()'s own setGamma()/setMarket() calls are already
      // simple replaces (not merges, unlike pricePoints/vwapPoints
      // above) because they're meant to always reflect the single most
      // recent fetch for whichever symbol is currently selected --
      // aborting the stale one is what keeps that true across a switch.
      interval = window.setInterval(
        () => void refresh(symbol, gammaViewRef.current, controller.signal),
        POLLING_INTERVAL_MS,
      );
      marketInterval = window.setInterval(() => {
        getMarket(symbol, controller.signal)
          .then((marketData) => {
            if (!controller.signal.aborted) applyMarket(marketData);
          })
          .catch(() => {
            // The 30 s full refresh reports errors; a missed 10 s market poll just waits for the next one.
          });
      }, MARKET_POLLING_INTERVAL_MS);
    };
    void seedThenPoll();

    return () => {
      controller.abort();
      if (interval !== undefined) window.clearInterval(interval);
      if (marketInterval !== undefined) window.clearInterval(marketInterval);
    };
  }, [applyMarket, refresh, seedPriceAndVwap, symbol]);

  // Semana: the candles of the last 5 trading dates come ready-made from the API (cached there for 30 s), so they are re-read every minute
  // and never accumulated from ticks. Day mode does not run any of this.
  useEffect(() => {
    if (!isWeek || !symbol) return;
    const controller = new AbortController();
    const load = () =>
      getPriceCandles(symbol, shownTimeframe as "5m" | "15m" | "1h", WEEK_SESSIONS, controller.signal)
        .then((response) => {
          if (!controller.signal.aborted) setWeekData({ key: `${symbol}|${shownTimeframe}`, candles: response.candles });
        })
        .catch((reason: unknown) => {
          if (!controller.signal.aborted) console.error("No se pudieron cargar las velas de la semana", reason);
        });
    void load();
    const interval = window.setInterval(() => void load(), 60_000);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [isWeek, symbol, shownTimeframe]);

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

  // v2 (desktop app windows): "Reducir movimiento", remembered in this browser / app.
  const [reduceMotion, setReduceMotion] = useState(false);
  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- reads the saved choice once after mount (localStorage does not exist on the server)
      setReduceMotion(window.localStorage.getItem(REDUCE_MOTION_STORAGE_KEY) === "1");
    } catch {
      // storage blocked: motion stays on
    }
  }, []);
  const toggleReduceMotion = (on: boolean) => {
    setReduceMotion(on);
    try {
      window.localStorage.setItem(REDUCE_MOTION_STORAGE_KEY, on ? "1" : "0");
    } catch {
      // storage blocked: the choice lasts for this window only
    }
  };
  const selectSymbol = (next: string) => {
    setGamma(null);
    setMarket(null);
    setPricePoints([]);
    setVwapPoints([]);
    setVwapNotApplicable(false);
    setVwapProxySymbol(null);
    setSymbol(next);
  };
  // "Ventanas -> abrir aparte": the layer's own /vista/grafico window on this symbol / timeframe / range / tab. The desktop app catches the window.open and opens
  // its layer window (desktop/main.js); a browser opens a tab or a window.
  const openLayerWindow = (layer: (typeof CHART_LAYERS)[number]) => {
    const query = chartRouteQuery({ symbol, timeframe, range: chartRange, gammaTab, layer });
    window.open(`/vista/grafico?${query}`, `convexa-${layer}`);
  };

  const v2Header = (
    <header className="v2-bar">
      <div className="v2-brand">
        <Image src="/logo-header.png" alt="Convexa — Volatility Exposure Edge" width={140} height={62} priority />
      </div>
      <div className="v2-row">
        <label>
          <span className="sr-only">{t.dashboard.underlyingLabel}</span>
          <select className="v2-sel" value={symbol} onChange={(event) => selectSymbol(event.target.value)} disabled={!underlyings.length}>
            {underlyings.map((item) => (
              <option key={item.symbol} value={item.symbol}>
                {item.symbol}
              </option>
            ))}
          </select>
        </label>
        {isFutureSymbol && (
          <FutureOpeningPriceControl
            key={`future-opening-price-v2-${symbol}`}
            symbol={symbol}
            onSaved={() => {
              void seedPriceAndVwap(symbol);
              void refresh(symbol, gammaViewRef.current);
            }}
          />
        )}
        {market && <strong className="v2-price num">{PRICE_FORMAT.format(market.price)}</strong>}
        {market && latestCandle && (
          <span className="v2-ohlc num">
            O {PRICE_FORMAT.format(latestCandle.open)} H {PRICE_FORMAT.format(latestCandle.high)} L {PRICE_FORMAT.format(latestCandle.low)} C{" "}
            {PRICE_FORMAT.format(latestCandle.close)}
          </span>
        )}
        <div className="v2-seg" role="group" aria-label={t.dashboard.timeframeGroupAriaLabel}>
          {(isWeek ? WEEK_TIMEFRAMES : TIMEFRAMES).map((option) => (
            <button key={option} type="button" aria-pressed={shownTimeframe === option} onClick={() => setTimeframe(option)}>
              {option}
            </button>
          ))}
        </div>
        {!isFutureSymbol && (
          <div className="v2-seg" role="group" aria-label={t.dashboard.rangeGroupAriaLabel}>
            {(["day", "week"] as const).map((option) => (
              <button key={option} type="button" aria-pressed={chartRange === option} onClick={() => setChartRange(option)}>
                {option === "day" ? t.dashboard.rangeDay : t.dashboard.rangeWeek}
              </button>
            ))}
          </div>
        )}
        <div className="v2-seg" role="group" aria-label={t.dashboard.gammaViewGroupAriaLabel} title={t.dashboard.gammaViewVwapNote}>
          {GAMMA_VIEW_TABS.map((tab) => (
            <button key={tab.view} type="button" aria-pressed={gammaTab === tab.view} onClick={() => selectGammaView(tab.view)}>
              {tab.label}
            </button>
          ))}
        </div>
        {expirationLabel && <span className="v2-expiration">{expirationLabel}</span>}
        <div
          className="v2-seg"
          role="group"
          aria-label={t.dashboard.gammaWeightGroupAriaLabel}
          title={supportsVolumeWeight(gammaTab) ? t.dashboard.gammaWeightNote : t.dashboard.gammaWeightOnly0dteNote}
        >
          {(["oi", "volume"] as const).map((weight) => (
            <button
              key={weight}
              type="button"
              aria-pressed={(supportsVolumeWeight(gammaTab) ? gammaWeight : "oi") === weight}
              disabled={weight === "volume" && !supportsVolumeWeight(gammaTab)}
              onClick={() => selectGammaWeight(weight)}
            >
              {weight === "oi" ? t.dashboard.gammaWeightOi : t.dashboard.gammaWeightVolume}
            </button>
          ))}
        </div>
        {gamma && <RegimeCompactBadge gamma={gamma} />}
        {market && <ExpectedMoveCompactBadge expectedMove={market.expected_move} />}
      </div>
      <div className="v2-row">
        <span className="v2-clock num">
          <SessionClock />
        </span>
        <div className="v2-seg" role="group" aria-label={t.common.languageSwitcherAriaLabel}>
          {(["es", "en"] as Language[]).map((option) => (
            <button key={option} type="button" aria-pressed={language === option} onClick={() => setLanguage(option)}>
              {option.toUpperCase()}
            </button>
          ))}
        </div>
        <V2Dropdown label={t.appV2.windowsMenu} className="v2-ws">
          {CHART_LAYERS.map((layer) => (
            <button key={layer} type="button" onClick={() => openLayerWindow(layer)}>
              {t.appV2.layers[layer] ?? layer}
              <span>⧉ {t.appV2.openApart}</span>
            </button>
          ))}
          <p>{t.appV2.windowsNote}</p>
        </V2Dropdown>
        <button type="button" className="v2-ibtn" title={t.appV2.guideTitle} aria-label={t.enginesGuide.triggerAriaLabel} onClick={() => setShowEnginesGuide(true)}>
          ?
        </button>
        <button
          type="button"
          className="v2-ibtn"
          title={t.appV2.logoutTitle}
          aria-label={t.dashboard.logoutButton}
          onClick={() => {
            void logout().finally(() => router.replace("/login"));
          }}
        >
          ⏻
        </button>
        <label className="v2-switch">
          <input type="checkbox" checked={reduceMotion} onChange={(event) => toggleReduceMotion(event.target.checked)} />
          {t.appV2.reduceMotion}
        </label>
      </div>
    </header>
  );

  return (
    <main className={`tv-shell${reduceMotion ? " reduce-motion" : ""}`}>
      {isChartOnly ? v2Header : (
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
          {isFutureSymbol && (
            <FutureOpeningPriceControl
              key={`future-opening-price-${symbol}`}
              symbol={symbol}
              onSaved={() => {
                void seedPriceAndVwap(symbol);
                void refresh(symbol, gammaViewRef.current);
              }}
            />
          )}
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
          {(isWeek ? WEEK_TIMEFRAMES : TIMEFRAMES).map((option) => (
            <button
              key={option}
              type="button"
              className="tv-timeframe"
              aria-pressed={shownTimeframe === option}
              onClick={() => setTimeframe(option)}
            >
              {option}
            </button>
          ))}
        </div>
        {/* The regime (long / short gamma and the net gamma amount) lives in the top bar, between the timeframes and the Day / Week selector. */}
        {gamma && <RegimeCompactBadge gamma={gamma} />}
        {/* The expected move in percent, next to the regime: the removed Expected Move panel's number, same source as the EMC / EMP lines. */}
        {market && <ExpectedMoveCompactBadge expectedMove={market.expected_move} />}
        {!isFutureSymbol && (
          <div className="tv-timeframes" role="group" aria-label={t.dashboard.rangeGroupAriaLabel}>
            {(["day", "week"] as const).map((option) => (
              <button
                key={option}
                type="button"
                className="tv-timeframe"
                aria-pressed={chartRange === option}
                onClick={() => setChartRange(option)}
              >
                {option === "day" ? t.dashboard.rangeDay : t.dashboard.rangeWeek}
              </button>
            ))}
          </div>
        )}
        <div
          className="tv-timeframes tv-gamma-views"
          role="group"
          aria-label={t.dashboard.gammaViewGroupAriaLabel}
          title={t.dashboard.gammaViewVwapNote}
        >
          {GAMMA_VIEW_TABS.map((tab) => (
            <button
              key={tab.view}
              type="button"
              className="tv-timeframe"
              aria-pressed={gammaTab === tab.view}
              onClick={() => selectGammaView(tab.view)}
            >
              {tab.label}
            </button>
          ))}
          {expirationLabel && <span className="tv-gamma-view-expiration">{expirationLabel}</span>}
        </div>
        <div
          className="tv-timeframes tv-gamma-weight"
          role="group"
          aria-label={t.dashboard.gammaWeightGroupAriaLabel}
          title={supportsVolumeWeight(gammaTab) ? t.dashboard.gammaWeightNote : t.dashboard.gammaWeightOnly0dteNote}
        >
          {(["oi", "volume"] as const).map((weight) => (
            <button
              key={weight}
              type="button"
              className="tv-timeframe"
              // On a tab without a volume twin the open interest is what is shown, whatever was chosen on 0DTE.
              aria-pressed={(supportsVolumeWeight(gammaTab) ? gammaWeight : "oi") === weight}
              disabled={weight === "volume" && !supportsVolumeWeight(gammaTab)}
              onClick={() => selectGammaWeight(weight)}
            >
              {weight === "oi" ? t.dashboard.gammaWeightOi : t.dashboard.gammaWeightVolume}
            </button>
          ))}
        </div>
        {!isChartOnly && <div className="tv-view-toggle" role="group" aria-label={t.dashboard.viewGroupAriaLabel}>
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
        </div>}
        <div className="tv-topbar-right">
          <SessionClock />
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
      )}

      {showEnginesGuide && (
        <EnginesGuidePanel onClose={() => setShowEnginesGuide(false)} />
      )}

      {hasNoOpeningPrice ? (
        <section className="panel status" role="status" aria-live="polite">
          {t.futureOpeningPrice.noDataMessage(symbol)}
        </section>
      ) : error && !(gamma && market) ? (
        <section className="panel status error" role="alert">
          {describeError(error, t)}
        </section>
      ) : gamma && market ? (
        <>
          {/* A failed request while the symbol's data is already on screen (a 2026-10-15 10:32 report: one
              failed call replaced the whole dashboard with this message): keep the last good data and
              show the failure as a notice. The next successful poll clears it. */}
          {error ? (
            <section className="panel status error dashboard-stale-notice" role="alert">
              {describeError(error, t)}
            </section>
          ) : null}
          {(() => {
          const alertsContent = (
            <aside className="tv-alerts-sidebar">
              <AlertsPanel symbol={symbol} orientation="vertical" />
            </aside>
          );
          // Under the chart, the Dealers group of the approved mockup: folded tabs that show only their number; one click opens the four cards together
          // (dealer impact, signal alignment, market bias, volatility regime). The aggregated greeks, the closing window, the expected-move text and the
          // Volatility Smile panel are gone on purpose (Lester, 2026-10-18): the heatmap tabs (delta, vanna, charm), the Smile dots on the AHORA line and the
          // expected-move lines of the chart already carry them. The per-minute flow bars panel will go between the chart and this group once it exists.
          const dealersDock = (
            <DealersPanel metrics={gamma.derived_metrics} />
          );
          const centerContent = (
            <div className="tv-center">
              {view === "live" ? (
                <>
                  <ChartErrorBoundary
                    key={`chart-boundary-${symbol}-${shownTimeframe}-${isWeek ? "week" : "day"}`}
                    label={t.dashboard.chartSelfHealLabel}
                    retryLabel={t.dashboard.chartSelfHealRetry}
                  >
                    <PriceChart
                      key={`price-chart-${symbol}-${shownTimeframe}-${isWeek ? "week" : "day"}`}
                      symbol={symbol}
                      candles={displayedCandles}
                      gamma={gamma}
                      gammaView={gammaView}
                      // VWAP restarts every session and the ATR / Expected Move bands are today's: none of them is drawn over a week.
                      vwapPoints={isWeek ? [] : vwapPoints}
                      vwapNotApplicable={isWeek ? true : vwapNotApplicable}
                      vwapProxySymbol={vwapProxySymbol}
                      atrRange={isWeek ? undefined : market.atr_range}
                      expectedMove={isWeek ? undefined : market.expected_move}
                      timeframe={shownTimeframe}
                      // The countdown belongs to the candle being formed: the week view has daily candles.
                      candleCountdown={!isWeek}
                      streamStatus={streamStatus}
                      layers={isChartOnly ? layerPreset(initialRoute?.layer ?? null) : undefined}
                      variant={isChartOnly ? "v2" : "classic"}
                    />
                  </ChartErrorBoundary>
                  {!isChartOnly && dealersDock}
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

          // Narrow viewports keep the original plain-flex stacked layout
          // (unchanged from before react-resizable-panels) rather than a
          // resizable one — Group's own inline flex-direction can't be
          // overridden by the existing @media stacking rule, and dragging
          // to resize isn't a meaningful gesture on a touch-sized screen
          // anyway. See useIsNarrowLayout above.
          // A /vista/grafico window is the chart alone: no alerts column (the alerts get their own window).
          if (isChartOnly) {
            return <div className="tv-body">{centerContent}</div>;
          }

          if (isNarrowLayout) {
            return (
              <div className="tv-body">
                {alertsContent}
                {centerContent}
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
            </Group>
          );
          })()}
        </>
      ) : (
        <section className="panel status" aria-live="polite">
          {t.dashboard.loadingRegime}
        </section>
      )}
    </main>
  );
}
