import { describe, expect, it } from "vitest";
import type { ReplayDayResponse } from "@/lib/types";
import {
  candlesAt,
  clampCursor,
  cursorRange,
  dayOptions,
  DEFAULT_REPLAY_SPEED,
  heatmapColumnsAt,
  initialDay,
  levelsAt,
  nextCursor,
  prepareDay,
  REPLAY_SPEEDS,
  replaySpeedLabel,
  sessionWhitespace,
  shiftHeatmap,
  sliceHeatmap,
  vwapAt,
} from "./replay";

const OPEN = Date.parse("2026-10-30T13:30:00Z") / 1000;
const iso = (seconds: number) => new Date(seconds * 1000).toISOString();
const candle = (minute: number, open: number, high: number, low: number, close: number) => ({ time: iso(OPEN + minute * 60), open, high, low, close });

const response: ReplayDayResponse = {
  schema_version: 1,
  symbol: "SPX",
  source_symbol: "SPX",
  session_date: "2026-10-30",
  view: "tactical",
  live: false,
  session_open: iso(OPEN),
  session_close: iso(OPEN + 390 * 60),
  price_offset: 0,
  anchored: null,
  candles_1m: [candle(0, 100, 101, 99, 100), candle(1, 100, 104, 100, 103), candle(5, 103, 106, 102, 105), candle(6, 105, 105, 98, 99)],
  candles_5m: [candle(0, 100, 104, 99, 103), candle(5, 103, 106, 98, 99)],
  levels: [
    { time: iso(OPEN + 20), call_wall: 110, put_wall: 90, absolute_gamma_strike: 100, gamma_flip: null },
    { time: iso(OPEN + 5 * 60 + 40), call_wall: 112, put_wall: 90, absolute_gamma_strike: 105, gamma_flip: 95 },
  ],
  vwap: [
    { time: iso(OPEN), value: 100 },
    { time: iso(OPEN + 60), value: 101 },
    { time: iso(OPEN + 300), value: 102 },
  ],
  vwap_not_applicable: false,
  vwap_proxy_symbol: "SPY",
};
const day = prepareDay(response);

describe("candlesAt", () => {
  it("1m shows every candle up to and including the cursor's minute, nothing after it", () => {
    expect(candlesAt(day, "1m", OPEN + 60).map((bar) => bar.close)).toEqual([100, 103]);
    expect(candlesAt(day, "1m", OPEN + 4 * 60)).toHaveLength(2);
  });

  it("5m keeps the finished candles and rebuilds the forming one from its minutes so far", () => {
    const bars = candlesAt(day, "5m", OPEN + 5 * 60);
    expect(bars).toEqual([
      { time: OPEN, open: 100, high: 104, low: 99, close: 103 },
      { time: OPEN + 300, open: 103, high: 106, low: 102, close: 105 },
    ]);
    // The stored 5-minute candle has the 09:36 low of 98: not on screen until the cursor reaches 09:36.
    expect(candlesAt(day, "5m", OPEN + 6 * 60).at(-1)).toEqual({ time: OPEN + 300, open: 103, high: 106, low: 98, close: 99 });
  });
});

describe("the rest of the minute", () => {
  it("levels are those of the last cycle stored before the minute ended", () => {
    expect(levelsAt(day, OPEN - 60)).toBeNull();
    expect(levelsAt(day, OPEN)).toEqual({ callWall: 110, putWall: 90, abs: 100, flip: null });
    expect(levelsAt(day, OPEN + 4 * 60)?.callWall).toBe(110);
    expect(levelsAt(day, OPEN + 5 * 60)).toEqual({ callWall: 112, putWall: 90, abs: 105, flip: 95 });
  });

  it("the VWAP stops at the cursor and has one point per candle", () => {
    expect(vwapAt(day, OPEN + 60).map((point) => point.value)).toEqual([100, 101]);
    expect(vwapAt(day, OPEN + 300, "5m")).toEqual([
      { time: OPEN, value: 101 },
      { time: OPEN + 300, value: 102 },
    ]);
  });

  it("the heatmap keeps only the columns started by the cursor and ES/NQ move its strikes", () => {
    const heatmap = { strikes: [100, 105], scale: 1, columns: [0, 120, 240].map((s) => ({ time: OPEN + s, values: [1, 2] })) };
    expect(heatmapColumnsAt(heatmap, OPEN + 119)).toBe(1);
    expect(heatmapColumnsAt(heatmap, OPEN + 240)).toBe(3);
    expect(sliceHeatmap(heatmap, 2).columns).toHaveLength(2);
    expect(sliceHeatmap(heatmap, 3)).toBe(heatmap);
    expect(shiftHeatmap(heatmap, 35).strikes).toEqual([135, 140]);
    expect(shiftHeatmap(heatmap, 0)).toBe(heatmap);
  });

  it("the time axis is padded to the close so it does not rescale while playing", () => {
    const slots = sessionWhitespace(day, "5m", OPEN + 300);
    expect(slots[0].time).toBe(OPEN + 600);
    expect(slots.at(-1)?.time).toBe(OPEN + 385 * 60);
    expect(sessionWhitespace(day, "1m", null)).toHaveLength(390);
  });
});

describe("cursor and days", () => {
  it("the cursor runs from 09:30 to the last stored minute and steps by one candle", () => {
    const range = cursorRange(day);
    expect(range).toEqual({ first: OPEN, last: OPEN + 6 * 60 });
    expect(clampCursor(OPEN + 999 * 60, range)).toBe(range.last);
    expect(nextCursor(OPEN, 1, "5m", range)).toBe(OPEN + 300);
    expect(nextCursor(OPEN, -1, "1m", range)).toBe(OPEN);
  });

  it("'Hoy (en vivo)' comes first, then the earlier sessions; the start day follows the address or the data", () => {
    const days = { schema_version: 1, symbol: "SPX", today: "2026-11-02", today_has_data: false, sessions: ["2026-10-30", "2026-10-29"] };
    expect(dayOptions(days)).toEqual([
      { value: "2026-11-02", today: true },
      { value: "2026-10-30", today: false },
      { value: "2026-10-29", today: false },
    ]);
    expect(initialDay(days, null)).toBe("2026-10-30");
    expect(initialDay({ ...days, today_has_data: true }, null)).toBe("2026-11-02");
    expect(initialDay(days, "2026-10-29")).toBe("2026-10-29");
    expect(initialDay(days, "2026-01-01")).toBe("2026-10-30");
  });
});

describe("replay speeds", () => {
  it("offers slower speeds than one minute per second and labels them as fractions", () => {
    expect(REPLAY_SPEEDS[0]).toBe(0.25);
    expect(replaySpeedLabel(0.25)).toBe("x¼");
    expect(replaySpeedLabel(0.5)).toBe("x½");
    expect(replaySpeedLabel(10)).toBe("x10");
    expect(DEFAULT_REPLAY_SPEED).toBe(0.5);
  });
});
