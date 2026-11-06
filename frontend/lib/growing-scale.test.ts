import { describe, expect, it } from "vitest";
import { columnScales } from "@/components/gamma-heatmap-primitive";
import { GROWING_MIN_BRIGHTNESS, GROWING_TARGET_RATIO, growingScales, usesGrowingScale } from "./growing-scale";

const strikes = [100, 105, 110];

describe("growing heatmap scale (ES 0DTE Volumen trial)", () => {
  it("applies only to ES on the 0DTE volume tab", () => {
    expect(usesGrowingScale("ES", "0dte_volume")).toBe(true);
    expect(usesGrowingScale("es", "0dte_volume")).toBe(true);
    expect(usesGrowingScale("ES", "0dte")).toBe(false);
    expect(usesGrowingScale("SPX", "0dte_volume")).toBe(false);
    expect(usesGrowingScale("NQ", "0dte_volume")).toBe(false);
  });

  it("colours a column against the OI column of the same moment, from the floor up to its own scale", () => {
    const oi = { strikes, scale: 1, columns: [{ time: 60, values: [10, 20, 10] }, { time: 120, values: [10, 20, 10] }] };
    const volume = {
      strikes,
      scale: 1,
      columns: [
        { time: 60, values: [1, 2, 1] }, // volume 0.1x the OI: the floor
        { time: 120, values: [50, 100, 50] }, // 5x: half way to the target
        { time: 130, values: [500, 1000, 500] }, // 50x, past the target: its own scale (the default view); OI taken from 120
      ],
    };
    expect(growingScales(volume, oi)).toEqual([2 / GROWING_MIN_BRIGHTNESS, GROWING_TARGET_RATIO * 20, 1000]);
  });

  it("falls back to the column's own scale without an OI column at or before it", () => {
    const oi = { strikes, scale: 1, columns: [{ time: 120, values: [10, 20, 10] }] };
    const volume = { strikes, scale: 1, columns: [{ time: 60, values: [1, 2, 1] }, { time: 90, values: [null, null, null] }] };
    expect(growingScales(volume, oi)).toEqual([2, null]);
  });

  it("is what columnScales draws with in the growing mode", () => {
    const data = { strikes, scale: 4000, scaleMode: "growing" as const, columnScaleList: [7, null], columns: [{ time: 1, values: [1, 2, 3] }, { time: 2, values: [4, 5, 6] }] };
    expect(columnScales(data)).toEqual([7, 6]);
  });
});
