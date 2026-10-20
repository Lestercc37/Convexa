import { describe, expect, it } from "vitest";
import { candleSecondsLeft, formatCountdown } from "./candle-countdown";

const T = Date.UTC(2026, 9, 20, 14, 42, 0) / 1000; // 10:42:00 ET, a 1-minute candle start

describe("candleSecondsLeft", () => {
  it("counts down to the end of the current candle", () => {
    expect(candleSecondsLeft(T, "1m", T + 31)).toBe(29);
    expect(candleSecondsLeft(T, "1m", T + 59.2)).toBe(1);
  });

  it("uses the bucket of 'now' when the next candle has not been created yet", () => {
    expect(candleSecondsLeft(T, "1m", T + 61)).toBe(59); // the 10:43 candle is still to come, but the countdown already belongs to it
  });

  it("works for the 5-minute, 15-minute and 1-hour candles", () => {
    const fiveStart = Date.UTC(2026, 9, 20, 14, 40, 0) / 1000;
    expect(candleSecondsLeft(fiveStart, "5m", fiveStart + 2 * 60 + 31)).toBe(149);
    const quarter = Date.UTC(2026, 9, 20, 14, 30, 0) / 1000;
    expect(candleSecondsLeft(quarter, "15m", quarter + 14 * 60)).toBe(60);
    const hour = Date.UTC(2026, 9, 20, 14, 0, 0) / 1000;
    expect(candleSecondsLeft(hour, "1h", hour + 20 * 60)).toBe(40 * 60);
  });

  it("is null without a candle, before it, or once the data is stale (closed market)", () => {
    expect(candleSecondsLeft(null, "1m", T)).toBeNull();
    expect(candleSecondsLeft(T, "1m", T - 1)).toBeNull();
    expect(candleSecondsLeft(T, "1m", T + 60 + 120)).toBeNull();
    expect(candleSecondsLeft(T, "1m", T + 60 + 119)).not.toBeNull();
  });
});

describe("formatCountdown", () => {
  it("prints mm:ss, and h:mm:ss from one hour", () => {
    expect(formatCountdown(29)).toBe("00:29");
    expect(formatCountdown(149)).toBe("02:29");
    expect(formatCountdown(3599)).toBe("59:59");
    expect(formatCountdown(3600)).toBe("1:00:00");
    expect(formatCountdown(-3)).toBe("00:00");
  });
});
