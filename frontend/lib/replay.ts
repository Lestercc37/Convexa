import type { HeatmapData } from "@/components/gamma-heatmap-primitive";
import type { ReplayDayResponse, ReplayDaysResponse, ReplayLevels } from "./types";

// The replay window's pure parts (docs/diseno-replay-5-dias-2026-10-29.txt): what is on screen at the chosen minute. The cursor is the START (unix
// seconds) of the last minute shown: candles up to and including that minute, the levels of the last cycle stored before it ends, the heatmap
// columns up to it. Nothing after the cursor is ever drawn.

export type ReplayTimeframe = "1m" | "5m";
export type Bar = { time: number; open: number; high: number; low: number; close: number };
export type LinePoint = { time: number; value: number };
export type LevelsAtMinute = { callWall: number | null; putWall: number | null; abs: number | null; flip: number | null };

export const REPLAY_SPEEDS = [1, 2, 5, 10] as const;
export type ReplaySpeed = (typeof REPLAY_SPEEDS)[number];
// x1 = one market minute per second; x10 = ten (a full session in 39 s).
export const MINUTE_SECONDS = 60;
export const TIMEFRAME_SECONDS: Record<ReplayTimeframe, number> = { "1m": 60, "5m": 300 };

export function toSeconds(iso: string): number {
  return Math.floor(Date.parse(iso) / 1000);
}

/** The day's arrays in seconds, converted once per response (the cursor moves many times a second at x10). */
export type PreparedDay = {
  open: number;
  close: number;
  candles1m: Bar[];
  candles5m: Bar[];
  levels: { time: number; levels: LevelsAtMinute }[];
  vwap: LinePoint[];
};

export function prepareDay(day: ReplayDayResponse): PreparedDay {
  const bars = (candles: ReplayDayResponse["candles_1m"]) =>
    candles.map((c) => ({ time: toSeconds(c.time), open: c.open, high: c.high, low: c.low, close: c.close }));
  return {
    open: toSeconds(day.session_open),
    close: toSeconds(day.session_close),
    candles1m: bars(day.candles_1m),
    candles5m: bars(day.candles_5m),
    levels: day.levels.map((cycle: ReplayLevels) => ({
      time: toSeconds(cycle.time),
      levels: { callWall: cycle.call_wall, putWall: cycle.put_wall, abs: cycle.absolute_gamma_strike, flip: cycle.gamma_flip },
    })),
    vwap: day.vwap.map((point) => ({ time: toSeconds(point.time), value: point.value })),
  };
}

/** First and last minute the cursor can take: 09:30 and the last stored 1-minute candle (09:30 when the day has none yet). */
export function cursorRange(day: PreparedDay): { first: number; last: number } {
  return { first: day.open, last: day.candles1m.at(-1)?.time ?? day.open };
}

export function clampCursor(cursor: number, range: { first: number; last: number }): number {
  return Math.min(range.last, Math.max(range.first, cursor));
}

/**
 * The candles on screen at the cursor. 1m: every candle up to the cursor's minute. 5m: the finished 5-minute candles plus the one forming at the
 * cursor, rebuilt from the 1-minute candles of its bucket so far (it must not show the rest of its five minutes).
 */
export function candlesAt(day: PreparedDay, timeframe: ReplayTimeframe, cursor: number): Bar[] {
  if (timeframe === "1m") return day.candles1m.filter((bar) => bar.time <= cursor);
  const step = TIMEFRAME_SECONDS["5m"];
  const bucket = day.open + Math.floor((cursor - day.open) / step) * step;
  const finished = day.candles5m.filter((bar) => bar.time < bucket);
  const inBucket = day.candles1m.filter((bar) => bar.time >= bucket && bar.time <= cursor);
  if (!inBucket.length) return finished;
  const forming: Bar = {
    time: bucket,
    open: inBucket[0].open,
    high: Math.max(...inBucket.map((bar) => bar.high)),
    low: Math.min(...inBucket.map((bar) => bar.low)),
    close: inBucket[inBucket.length - 1].close,
  };
  return [...finished, forming];
}

/** Empty slots from the candle after `lastTime` to the close, so the time axis always spans the whole session and does not rescale as the cursor moves. */
export function sessionWhitespace(day: PreparedDay, timeframe: ReplayTimeframe, lastTime: number | null): { time: number }[] {
  const step = TIMEFRAME_SECONDS[timeframe];
  const slots: { time: number }[] = [];
  for (let time = lastTime === null ? day.open : lastTime + step; time < day.close; time += step) slots.push({ time });
  return slots;
}

/** The levels of the last cycle stored before the cursor's minute ended (null before the day's first cycle). */
export function levelsAt(day: PreparedDay, cursor: number): LevelsAtMinute | null {
  const minuteEnd = cursor + MINUTE_SECONDS;
  let found: LevelsAtMinute | null = null;
  for (const cycle of day.levels) {
    if (cycle.time >= minuteEnd) break;
    found = cycle.levels;
  }
  return found;
}

/**
 * The VWAP up to the cursor, one point per candle (the last value of its bucket, at the candle's time). On 5m a point per minute would add the minutes to
 * the time axis between the candles (the heatmap then drew in stripes and the session no longer fitted), the same reason aggregateVwapPoints exists.
 */
export function vwapAt(day: PreparedDay, cursor: number, timeframe: ReplayTimeframe = "1m"): LinePoint[] {
  const step = TIMEFRAME_SECONDS[timeframe];
  const byBucket = new Map<number, number>();
  for (const point of day.vwap) {
    if (point.time > cursor) break;
    byBucket.set(day.open + Math.floor((point.time - day.open) / step) * step, point.value);
  }
  return [...byBucket.entries()].map(([time, value]) => ({ time, value }));
}

/** How many heatmap columns are on screen at the cursor (their buckets started by the cursor's minute). */
export function heatmapColumnsAt(heatmap: HeatmapData, cursor: number): number {
  let count = 0;
  for (const column of heatmap.columns) {
    if (column.time > cursor) break;
    count += 1;
  }
  return count;
}

export function sliceHeatmap(heatmap: HeatmapData, count: number): HeatmapData {
  return count >= heatmap.columns.length ? heatmap : { ...heatmap, columns: heatmap.columns.slice(0, count) };
}

/** ES / NQ: the index's heatmap with every strike moved by that day's offset (0 = the index's own strikes). */
export function shiftHeatmap(heatmap: HeatmapData, offset: number): HeatmapData {
  return offset === 0 ? heatmap : { ...heatmap, strikes: heatmap.strikes.map((strike) => strike + offset) };
}

export type DayOption = { value: string; today: boolean };

/** "Hoy (en vivo)" first (always: before the open it simply has nothing yet), then the earlier sessions with data, newest first. */
export function dayOptions(days: ReplayDaysResponse): DayOption[] {
  return [{ value: days.today, today: true }, ...days.sessions.filter((day) => day !== days.today).map((day) => ({ value: day, today: false }))];
}

/** The day the window starts on: the one in the address if offered, else today when it has data, else the latest earlier session. */
export function initialDay(days: ReplayDaysResponse, requested: string | null): string {
  if (requested && (requested === days.today || days.sessions.includes(requested))) return requested;
  return days.today_has_data || !days.sessions.length ? days.today : days.sessions[0];
}

export function nextCursor(cursor: number, direction: 1 | -1, timeframe: ReplayTimeframe, range: { first: number; last: number }): number {
  return clampCursor(cursor + direction * TIMEFRAME_SECONDS[timeframe], range);
}

const etFormatter = new Intl.DateTimeFormat("es-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false });

export function etClock(seconds: number): string {
  return etFormatter.format(new Date(seconds * 1000));
}
