import { describe, expect, it } from "vitest";
import { aggregateMinuteCandles } from "./candles";

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
