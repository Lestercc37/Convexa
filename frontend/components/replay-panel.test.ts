import { describe, expect, it } from "vitest";
import type { HeatmapData } from "./gamma-heatmap-primitive";
import { replayHeatmapMetrics, replayProfileBars } from "./replay-panel";

const OFF = { calls: false, puts: false };
// Three stored 2-minute columns (10:00, 10:02, 10:04 ET of 2026-11-05) over two strikes.
const T0 = Date.parse("2026-11-05T15:00:00Z") / 1000;
const heatmap = (values: (number | null)[][]): HeatmapData => ({
  strikes: [7600, 7605],
  scale: 1,
  columns: values.map((column, index) => ({ time: T0 + index * 120, values: column })),
});

describe("replay heatmap figure", () => {
  it("asks for the chosen figure, or for each side switched on instead of it", () => {
    expect(replayHeatmapMetrics("absolute_gamma", OFF)).toEqual(["absolute_gamma"]);
    expect(replayHeatmapMetrics("gamma", { calls: true, puts: false })).toEqual(["call_gamma"]);
    expect(replayHeatmapMetrics("delta", { calls: true, puts: true })).toEqual(["call_gamma", "put_gamma"]);
  });
});

describe("replay GEX profile at the cursor", () => {
  const gamma = heatmap([
    [1, -2],
    [3, null],
    [5, 6],
  ]);

  it("takes each strike's value from the last stored cycle up to the cursor, never a later one", () => {
    expect(replayProfileBars({ gamma }, "gamma", OFF, T0 + 150)).toEqual([
      { strike: 7600, net: 3, absolute: 3 },
      { strike: 7605, net: -2, absolute: 2 },
    ]);
    expect(replayProfileBars({ gamma }, "gamma", OFF, T0 + 240).map((bar) => bar.net)).toEqual([5, 6]);
    expect(replayProfileBars({ gamma }, "gamma", OFF, T0 - 60)).toEqual([]);
  });

  it("follows the chosen figure and draws calls and puts apart when the sides are on", () => {
    const calls = heatmap([[2, 4]]);
    const puts = heatmap([[-1, -3]]);
    expect(replayProfileBars({ absolute_gamma: heatmap([[7, 8]]) }, "absolute_gamma", OFF, T0).map((bar) => bar.net)).toEqual([7, 8]);
    expect(replayProfileBars({ call_gamma: calls, put_gamma: puts }, "gamma", { calls: true, puts: true }, T0)).toEqual([
      { strike: 7600, net: 1, absolute: 3, call: 2, put: -1 },
      { strike: 7605, net: 1, absolute: 7, call: 4, put: -3 },
    ]);
    expect(replayProfileBars({ call_gamma: calls }, "gamma", { calls: true, puts: false }, T0)[0].put).toBeUndefined();
  });
});
