"use client";

import Image from "next/image";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  CrosshairMode,
  LineSeries,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { getGammaHeatmap, getReplayDay, getReplayDays } from "@/lib/api";
import { useLanguage } from "@/lib/i18n/language-context";
import {
  type Bar,
  candlesAt,
  clampCursor,
  cursorRange,
  dayOptions,
  etClock,
  heatmapColumnsAt,
  initialDay,
  type LevelsAtMinute,
  levelsAt,
  type LinePoint,
  MINUTE_SECONDS,
  nextCursor,
  prepareDay,
  DEFAULT_REPLAY_SPEED,
  REPLAY_SPEEDS,
  replaySpeedLabel,
  type ReplaySpeed,
  type ReplayTimeframe,
  sessionWhitespace,
  shiftHeatmap,
  sliceHeatmap,
  vwapAt,
} from "@/lib/replay";
import { APP_GAMMA_VIEW_TABS, effectiveGammaView, supportsVolumeWeight, type GammaWeight } from "@/lib/gamma-views";
import type { GammaView, ReplayDayResponse, ReplayDaysResponse } from "@/lib/types";
import { GammaHeatmapPrimitive, heatmapFromResponse, type HeatmapData } from "./gamma-heatmap-primitive";

/**
 * Replay of a stored session (docs/diseno-replay-5-dias-2026-10-29.txt, phase 1): candles up to the chosen minute, the heatmap up to it, the levels
 * each stored cycle had at that minute and the day's anchored VWAP, with play / pause / step / speed and a scrub bar. "Hoy (en vivo)" replays today
 * from 09:30 and, once the replay reaches the present (or "Ir a en vivo"), follows the session live. Data: GET /replay/{symbol}/day (one request per
 * day) and the existing /gamma/{symbol}/heatmap with that day's window in 2-minute buckets (the stored cycle is ~1.6 min).
 */
const LIVE_POLL_MS = 10_000;
const HEATMAP_LIVE_POLL_MS = 30_000;
const RETRY_MS = 10_000;
const HEATMAP_BUCKET_SECONDS = 120;
// The app's own tabs (0DTE | 1DTE | 90 días) and OI | Volumen, so the replay shows what was seen live (Lester 2026-11-03: the 0-2 DTE view it
// used first painted the 7575 band an hour later than his 0DTE chart). Starts on 0DTE by volume, his request.
const DEFAULT_TAB: GammaView = "0dte";
const DEFAULT_WEIGHT: GammaWeight = "volume";
const VWAP_COLOR = "#f3c969";
const LEVEL_LINES: { key: keyof LevelsAtMinute; title: string; color: string }[] = [
  { key: "callWall", title: "Call Wall", color: "#00DC5A" },
  { key: "putWall", title: "Put Wall", color: "#FA000A" },
  { key: "abs", title: "Abs. Gamma", color: "#7eb6ff" },
  { key: "flip", title: "Gamma Flip", color: "#f3c969" },
];

type ReplayChartProps = {
  bars: Bar[];
  whitespace: { time: number }[];
  vwap: LinePoint[];
  levels: LevelsAtMinute | null;
  heatmap: HeatmapData | null;
  // A new day or timeframe: the time axis is fitted to the whole session once.
  fitKey: string;
};

function ReplayChart({ bars, whitespace, vwap, levels, heatmap, fitKey }: ReplayChartProps) {
  const container = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candlesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const vwapRef = useRef<ISeriesApi<"Line"> | null>(null);
  const heatmapRef = useRef<GammaHeatmapPrimitive | null>(null);
  const linesRef = useRef<Map<keyof LevelsAtMinute, IPriceLine>>(new Map());
  const fittedKeyRef = useRef("");
  // Logical slots of the whole session (candles + empty slots to the close): the axis is fitted to them, not just to the candles drawn so far.
  const slotsRef = useRef(0);
  // Until the viewer zooms or pans, the window keeps the whole session in view (also after a resize); after that their own view is never fought.
  const userAdjustedRef = useRef(false);

  // The whole session on screen, 09:30 at the left and 16:00 at the right, so the axis never moves while the replay plays.
  const fitSession = () => {
    const chart = chartRef.current;
    if (!chart || slotsRef.current === 0) return;
    chart.timeScale().setVisibleLogicalRange({ from: -0.5, to: slotsRef.current - 0.5 });
  };

  useEffect(() => {
    if (!container.current) return;
    // The main chart's look: near-black body, ET times, no TradingView logo (Convexa's watermark is drawn over the frame below).
    const chart = createChart(container.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#050608" }, textColor: "#787b86", attributionLogo: false },
      grid: { vertLines: { color: "rgba(42, 46, 57, 0.6)" }, horzLines: { color: "rgba(42, 46, 57, 0.6)" } },
      rightPriceScale: { borderColor: "#2A2E39" },
      // lockVisibleTimeRangeOnResize: autoSize's first real width arrives after the data, and the default (keep the bar spacing) shrank the whole day to its first minutes.
      timeScale: { timeVisible: true, secondsVisible: false, lockVisibleTimeRangeOnResize: true, tickMarkFormatter: (time: UTCTimestamp) => etClock(time) },
      localization: { timeFormatter: (time: UTCTimestamp) => etClock(time) },
      crosshair: { mode: CrosshairMode.Normal },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#26A69A",
      downColor: "#EF5350",
      borderVisible: false,
      wickUpColor: "#26A69A",
      wickDownColor: "#EF5350",
    });
    const vwapSeries = chart.addSeries(LineSeries, {
      color: VWAP_COLOR,
      lineWidth: 2,
      lineStyle: LineStyle.Dotted,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      title: "VWAP",
    });
    const primitive = new GammaHeatmapPrimitive({ smooth: true });
    candles.attachPrimitive(primitive);
    for (const line of LEVEL_LINES) {
      linesRef.current.set(
        line.key,
        candles.createPriceLine({ price: 0, color: line.color, lineWidth: 2, lineStyle: LineStyle.Solid, title: line.title, lineVisible: false, axisLabelVisible: false }),
      );
    }
    chartRef.current = chart;
    candlesRef.current = candles;
    vwapRef.current = vwapSeries;
    heatmapRef.current = primitive;
    const lines = linesRef.current;
    const element = container.current;
    const markUserAdjusted = () => {
      userAdjustedRef.current = true;
    };
    element.addEventListener("wheel", markUserAdjusted, { passive: true });
    element.addEventListener("pointerdown", markUserAdjusted, { passive: true });
    const onSize = (width: number) => {
      if (width > 0 && !userAdjustedRef.current) fitSession();
    };
    chart.timeScale().subscribeSizeChange(onSize);
    return () => {
      element.removeEventListener("wheel", markUserAdjusted);
      element.removeEventListener("pointerdown", markUserAdjusted);
      chart.timeScale().unsubscribeSizeChange(onSize);
      chart.remove();
      chartRef.current = null;
      candlesRef.current = null;
      vwapRef.current = null;
      heatmapRef.current = null;
      lines.clear();
    };
  }, []);

  useEffect(() => {
    const series = candlesRef.current;
    if (!series) return;
    series.setData([
      ...bars.map((bar) => ({ ...bar, time: bar.time as UTCTimestamp })),
      ...whitespace.map((slot) => ({ time: slot.time as UTCTimestamp })),
    ]);
    slotsRef.current = bars.length + whitespace.length;
  }, [bars, whitespace]);

  useEffect(() => {
    vwapRef.current?.setData(vwap.map((point) => ({ time: point.time as UTCTimestamp, value: point.value })));
  }, [vwap]);

  // After BOTH series have the new day / timeframe: the time axis is the union of their times, so fitting earlier (with the other timeframe's VWAP
  // points still in it) fitted the wrong number of slots.
  useEffect(() => {
    if (fittedKeyRef.current === fitKey || slotsRef.current === 0) return;
    fittedKeyRef.current = fitKey;
    userAdjustedRef.current = false;
    fitSession();
  }, [bars, whitespace, vwap, fitKey]);

  useEffect(() => {
    for (const line of LEVEL_LINES) {
      const price = levels?.[line.key] ?? null;
      linesRef.current.get(line.key)?.applyOptions(
        price === null ? { lineVisible: false, axisLabelVisible: false } : { price, lineVisible: true, axisLabelVisible: true },
      );
    }
  }, [levels]);

  useEffect(() => {
    heatmapRef.current?.setData(heatmap);
  }, [heatmap]);

  return (
    <div className="v2-replay-frame">
      <div ref={container} className="v2-replay-chart" />
      <Image src="/logo-watermark.png" alt="" width={360} height={215} className="chart-watermark" aria-hidden="true" />
    </div>
  );
}

const offsetFormat = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "always" });

export function ReplayPanel({ symbol }: { symbol: string }) {
  const { t } = useLanguage();
  const params = useSearchParams();
  const requestedDay = params.get("dia");
  const [days, setDays] = useState<ReplayDaysResponse | null>(null);
  const [daysFailed, setDaysFailed] = useState(false);
  const [day, setDay] = useState<string | null>(null);
  const [timeframe, setTimeframe] = useState<ReplayTimeframe>("1m");
  // The loaded day with the key it was asked for, so a slow answer for another day is never drawn under this one.
  const [loaded, setLoaded] = useState<{ key: string; data: ReplayDayResponse } | null>(null);
  const [dayFailed, setDayFailed] = useState(false);
  const [heatmap, setHeatmap] = useState<{ key: string; data: HeatmapData } | null>(null);
  // Where the replay stands, remembered with the day it belongs to: another day (or symbol) reads as "not set", so it starts at 09:30 paused.
  const [position, setPosition] = useState<{ key: string; cursor: number; following: boolean } | null>(null);
  const [playingKey, setPlayingKey] = useState<string | null>(null);
  const [speed, setSpeed] = useState<ReplaySpeed>(DEFAULT_REPLAY_SPEED);
  const [gammaTab, setGammaTab] = useState<GammaView>(DEFAULT_TAB);
  const [gammaWeight, setGammaWeight] = useState<GammaWeight>(DEFAULT_WEIGHT);
  const view = effectiveGammaView(gammaTab, gammaWeight);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    const load = () =>
      getReplayDays(symbol, controller.signal)
        .then((response) => {
          setDays(response);
          setDaysFailed(false);
          setDay((current) => current ?? initialDay(response, requestedDay));
        })
        .catch(() => {
          if (controller.signal.aborted) return;
          setDaysFailed(true);
          timer = window.setTimeout(load, RETRY_MS);
        });
    void load();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [symbol, requestedDay]);

  const dayKey = day ? `${symbol}|${day}` : "";
  // The loaded data also depends on the view; the cursor (dayKey) does not, so changing the view keeps the replay where it is.
  const dataKey = day ? `${dayKey}|${view}` : "";
  const isToday = days !== null && day === days.today;
  const data = loaded && loaded.key === dataKey ? loaded.data : null;
  const live = data?.live ?? false;

  // The day's data: once for a finished day, every 10 s while today's session is open (the server holds it 5 s, shared by every window).
  useEffect(() => {
    if (!day) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const load = () =>
      getReplayDay(symbol, day, view, controller.signal)
        .then((response) => {
          setLoaded({ key: `${symbol}|${day}|${view}`, data: response });
          setDayFailed(false);
          if (response.live) timer = window.setTimeout(load, LIVE_POLL_MS);
        })
        .catch(() => {
          if (controller.signal.aborted) return;
          setDayFailed(true);
          timer = window.setTimeout(load, RETRY_MS);
        });
    void load();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [symbol, day, view]);

  // The heatmap of the day's window from the existing endpoint, in the source's strikes (SPX for ES) moved by that day's offset.
  const heatmapKey = data ? `${dataKey}|${data.source_symbol}|${data.price_offset}` : "";
  useEffect(() => {
    if (!data) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const key = heatmapKey;
    const load = () =>
      getGammaHeatmap(
        data.source_symbol,
        {
          view,
          metric: "gamma",
          start: new Date(data.session_open),
          end: data.live ? undefined : new Date(data.session_close),
          bucketSeconds: HEATMAP_BUCKET_SECONDS,
        },
        controller.signal,
      )
        .then((response) => {
          const shifted = shiftHeatmap(heatmapFromResponse(response), data.price_offset);
          setHeatmap({ key, data: { ...shifted, scaleMode: "column" } });
        })
        .catch(() => {
          // No heatmap (an older session without per-strike rows, a failed read): the candles and levels still replay.
        })
        .finally(() => {
          if (!controller.signal.aborted && data.live) timer = window.setTimeout(load, HEATMAP_LIVE_POLL_MS);
        });
    void load();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
    // Re-read only when the day (or its offset) changes; today's own poll above keeps `data` fresh without restarting this one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [heatmapKey, live]);

  const prepared = useMemo(() => (data ? prepareDay(data) : null), [data]);
  const range = useMemo(() => (prepared ? cursorRange(prepared) : null), [prepared]);
  // A new day starts at 09:30 (today too: "Hoy (en vivo)" replays the morning first); while following, today's refreshes move it to the present.
  const held = position && position.key === dayKey ? position : null;
  const following = held?.following ?? false;
  const playing = playingKey === dayKey;
  const shownCursor = range ? (following ? range.last : clampCursor(held?.cursor ?? range.first, range)) : null;

  const moveTo = (next: number) => {
    if (!range) return;
    const clamped = clampCursor(next, range);
    setPosition({ key: dayKey, cursor: clamped, following: live && clamped >= range.last });
  };
  const tickRef = useRef<() => void>(() => {});
  useEffect(() => {
    // One market minute per tick. Today: reaching the present switches to live; a finished day simply stops at its last minute.
    tickRef.current = () => {
      if (!range || shownCursor === null) return;
      const next = clampCursor(shownCursor + MINUTE_SECONDS, range);
      moveTo(next);
      if (next >= range.last) setPlayingKey(null);
    };
  });
  useEffect(() => {
    if (!playing) return;
    const interval = window.setInterval(() => tickRef.current(), 1000 / speed);
    return () => window.clearInterval(interval);
  }, [playing, speed]);

  const bars = useMemo(() => (prepared && shownCursor !== null ? candlesAt(prepared, timeframe, shownCursor) : []), [prepared, timeframe, shownCursor]);
  const lastBarTime = bars.at(-1)?.time ?? null;
  const whitespace = useMemo(() => (prepared ? sessionWhitespace(prepared, timeframe, lastBarTime) : []), [prepared, timeframe, lastBarTime]);
  const vwap = useMemo(() => (prepared && shownCursor !== null ? vwapAt(prepared, shownCursor, timeframe) : []), [prepared, shownCursor, timeframe]);
  const levels = useMemo(() => (prepared && shownCursor !== null ? levelsAt(prepared, shownCursor) : null), [prepared, shownCursor]);
  const dayHeatmap = heatmap && heatmap.key === heatmapKey ? heatmap.data : null;
  const columnCount = dayHeatmap && shownCursor !== null ? heatmapColumnsAt(dayHeatmap, shownCursor) : 0;
  // Sliced only when a new 2-minute column comes into view, so the heatmap image is rebuilt a few times a second at most, not every tick.
  const visibleHeatmap = useMemo(() => (dayHeatmap ? sliceHeatmap(dayHeatmap, columnCount) : null), [dayHeatmap, columnCount]);

  const step = (direction: 1 | -1) => {
    if (!range || shownCursor === null) return;
    setPlayingKey(null);
    moveTo(nextCursor(shownCursor, direction, timeframe, range));
  };

  const hasCandles = (prepared?.candles1m.length ?? 0) > 0;
  const options = days ? dayOptions(days) : [];
  const future = data && data.source_symbol !== data.symbol;

  return (
    <div className="v2-replay">
      <div className="v2-chain-head">
        <label className="v2-dim">
          {t.replay.day}{" "}
          <select className="v2-sel" aria-label={t.replay.day} value={day ?? ""} onChange={(event) => setDay(event.target.value)} disabled={!days}>
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.today ? t.replay.todayLive : option.value}
              </option>
            ))}
          </select>
        </label>
        <label className="v2-dim">
          {t.replay.timeframe}{" "}
          <select className="v2-sel" aria-label={t.replay.timeframe} value={timeframe} onChange={(event) => setTimeframe(event.target.value as ReplayTimeframe)}>
            <option value="1m">1m</option>
            <option value="5m">5m</option>
          </select>
        </label>
        <div className="v2-seg" role="group" aria-label={t.replay.viewGroup}>
          {APP_GAMMA_VIEW_TABS.map((tab) => (
            <button key={tab.view} type="button" aria-pressed={gammaTab === tab.view} onClick={() => setGammaTab(tab.view)}>
              {tab.label}
            </button>
          ))}
        </div>
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
              onClick={() => setGammaWeight(weight)}
            >
              {weight === "oi" ? t.dashboard.gammaWeightOi : t.dashboard.gammaWeightVolume}
            </button>
          ))}
        </div>
        <span className="v2-replay-controls">
          <button type="button" className="v2-ibtn" title={t.replay.stepBack} aria-label={t.replay.stepBack} onClick={() => step(-1)} disabled={!hasCandles}>
            ⏮
          </button>
          <button
            type="button"
            className="v2-ibtn"
            title={playing ? t.replay.pause : t.replay.play}
            aria-label={playing ? t.replay.pause : t.replay.play}
            onClick={() => {
              if (playing) {
                setPlayingKey(null);
                return;
              }
              // From the end (or from live), play starts the day over; otherwise it continues from where the cursor is.
              if (range && shownCursor !== null) moveTo(shownCursor >= range.last ? range.first : shownCursor);
              setPlayingKey(dayKey);
            }}
            disabled={!hasCandles}
          >
            {playing ? "❚❚" : "▶"}
          </button>
          <button type="button" className="v2-ibtn" title={t.replay.stepForward} aria-label={t.replay.stepForward} onClick={() => step(1)} disabled={!hasCandles}>
            ⏭
          </button>
        </span>
        <span className="v2-replay-speeds" role="group" aria-label={t.replay.speed}>
          {REPLAY_SPEEDS.map((option) => (
            <button key={option} type="button" className="v2-btn" aria-pressed={speed === option} onClick={() => setSpeed(option)}>
              {replaySpeedLabel(option)}
            </button>
          ))}
        </span>
        {shownCursor !== null && range && hasCandles && (
          <b className={following ? "v2-up" : "v2-chain-past"}>
            {live
              ? following
                ? t.replay.liveAt(etClock(range.last))
                : t.replay.replayAt(etClock(shownCursor), etClock(range.last))
              : etClock(shownCursor)}
          </b>
        )}
        {live && !following && (
          <button
            type="button"
            className="v2-btn"
            onClick={() => {
              setPlayingKey(null);
              if (range) setPosition({ key: dayKey, cursor: range.last, following: true });
            }}
          >
            {t.replay.goLive}
          </button>
        )}
      </div>
      {future && data.anchored === false && <p className="v2-chain-past v2-replay-note">{t.replay.noAnchor(data.symbol, data.source_symbol, data.session_date)}</p>}
      {future && data.anchored && (
        <p className="v2-dim v2-replay-note">{t.replay.anchoredOn(data.symbol, data.source_symbol, offsetFormat.format(data.price_offset))}</p>
      )}
      {daysFailed && !days ? (
        <p className="v2-panel-note">{t.replay.failed}</p>
      ) : dayFailed && !data ? (
        <p className="v2-panel-note">{t.replay.failed}</p>
      ) : !data ? (
        <p className="v2-panel-note">{t.replay.loading}</p>
      ) : !hasCandles ? (
        <p className="v2-panel-note">{isToday ? t.replay.noDataToday : t.replay.noData}</p>
      ) : (
        <>
          <ReplayChart bars={bars} whitespace={whitespace} vwap={vwap} levels={levels} heatmap={visibleHeatmap} fitKey={`${dayKey}|${timeframe}`} />
          {range && shownCursor !== null && (
            <input
              type="range"
              className="v2-replay-scrub"
              aria-label={t.replay.scrub}
              min={range.first}
              max={range.last}
              step={MINUTE_SECONDS}
              value={shownCursor}
              onChange={(event) => moveTo(Number(event.target.value))}
            />
          )}
        </>
      )}
      <p className="v2-chain-hint">
        {t.replay.hint}
        {data?.vwap_proxy_symbol ? ` ${t.replay.vwapProxy(data.vwap_proxy_symbol)}.` : ""}
      </p>
    </div>
  );
}
