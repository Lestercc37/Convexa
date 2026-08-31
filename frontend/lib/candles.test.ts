import { describe, expect, it } from "vitest";
import { aggregateCandles, aggregateMinuteCandles, type MinuteCandle } from "./candles";

describe("aggregateMinuteCandles", () => {
  it("builds one-minute OHLC candles from timestamped price points", () => {
    const candles = aggregateMinuteCandles([
      { timestamp: "2026-08-03T14:30:45Z", price: 550 },
      { timestamp: "2026-08-03T14:30:05Z", price: 548 },
      { timestamp: "2026-08-03T14:30:20Z", price: 552 },
      { timestamp: "2026-08-03T14:31:05Z", price: 551 },
      { timestamp: "2026-08-03T14:31:35Z", price: 549 },
    ]);

    expect(candles).toEqual([
      {
        time: Date.parse("2026-08-03T14:30:00Z") / 1000,
        open: 548,
        high: 552,
        low: 548,
        close: 550,
      },
      {
        time: Date.parse("2026-08-03T14:31:00Z") / 1000,
        open: 551,
        high: 551,
        low: 549,
        close: 549,
      },
    ]);
  });
});

describe("aggregateCandles", () => {
  // Six one-minute candles, one minute apart, times in plain seconds so the
  // expected bucket boundaries can be checked by hand.
  const oneMinuteCandles: MinuteCandle[] = [
    { time: 0, open: 100, high: 105, low: 99, close: 102 },
    { time: 60, open: 102, high: 107, low: 101, close: 106 },
    { time: 120, open: 106, high: 108, low: 104, close: 105 },
    { time: 180, open: 105, high: 110, low: 103, close: 109 },
    { time: 240, open: 109, high: 111, low: 108, close: 110 },
    { time: 300, open: 110, high: 112, low: 109, close: 111 },
  ];

  it("returns the input unchanged for the 1m timeframe", () => {
    expect(aggregateCandles(oneMinuteCandles, "1m")).toBe(oneMinuteCandles);
  });

  it("groups five 1-minute candles into a 5-minute bucket, by elapsed time", () => {
    expect(aggregateCandles(oneMinuteCandles, "5m")).toEqual([
      // t=0..240 (5 candles) fall in the [0, 300) bucket: open of the
      // first, close of the last, max high, min low across all five.
      { time: 0, open: 100, high: 111, low: 99, close: 110 },
      // t=300 alone starts the next 5-minute bucket.
      { time: 300, open: 110, high: 112, low: 109, close: 111 },
    ]);
  });

  it("groups all six 1-minute candles into a single 15-minute bucket", () => {
    expect(aggregateCandles(oneMinuteCandles, "15m")).toEqual([
      { time: 0, open: 100, high: 112, low: 99, close: 111 },
    ]);
  });

  it("splits candles across an hour boundary for the 1h timeframe", () => {
    const candles: MinuteCandle[] = [
      { time: 0, open: 200, high: 205, low: 198, close: 202 },
      { time: 1_800, open: 202, high: 207, low: 200, close: 203 },
      { time: 3_600, open: 203, high: 206, low: 201, close: 204 },
    ];

    expect(aggregateCandles(candles, "1h")).toEqual([
      { time: 0, open: 200, high: 207, low: 198, close: 203 },
      { time: 3_600, open: 203, high: 206, low: 201, close: 204 },
    ]);
  });
});
