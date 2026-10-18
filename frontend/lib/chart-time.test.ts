import { describe, expect, it } from "vitest";
import { timeToX } from "./chart-time";

// 5-minute bars at t = 0, 300, 600, ... (index = t / 300), 30 px apart starting at x = 50.
const BARS = 10;
const timeScale = {
  timeToIndex: (time: number) => Math.max(0, Math.min(BARS - 1, Math.round(time / 300))),
  logicalToCoordinate: (index: number) => 50 + index * 30,
};
const series = { dataByIndex: (index: number) => (index >= 0 && index < BARS ? { time: index * 300 } : null) };
const x = (time: number) => timeToX(timeScale as never, series as never, time);

describe("timeToX", () => {
  it("is the bar's own x for a time that is a bar", () => {
    expect(x(600)).toBe(110);
  });

  it("moves between the two bars around a time that is not a bar, in proportion", () => {
    expect(x(660)).toBe(116); // 60 s after the bar at 600 (x 110), a fifth of the way to x 140
    expect(x(540)).toBe(104); // 60 s before the bar at 600: nearest bar is 600, neighbour 300 (x 80)
  });

  it("answers for a time shortly past the last bar or before the first, and refuses far beyond", () => {
    expect(x(9 * 300 + 120)).toBe(50 + 9 * 30); // after the last bar: its x
    expect(x(-120)).toBe(50);
    expect(x(9 * 300 + 7200)).toBeNull();
  });

  it("is null when the time scale has no index for it", () => {
    const empty = { timeToIndex: () => null, logicalToCoordinate: () => 0 };
    expect(timeToX(empty as never, series as never, 100)).toBeNull();
  });
});
