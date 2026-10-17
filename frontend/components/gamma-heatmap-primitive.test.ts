import { describe, expect, it, vi } from "vitest";
import {
  cellColor,
  GammaHeatmapPrimitive,
  HEATMAP_MINIMUM_SHARE,
  HEATMAP_NEGATIVE_STOPS,
  HEATMAP_POSITIVE_STOPS,
  rampColor,
  type HeatmapData,
} from "./gamma-heatmap-primitive";

type FakeContext = { fillStyle: string; fillRect: ReturnType<typeof vi.fn>; fills: { color: string; rect: number[] }[] };

function fakeContext(): FakeContext {
  const context: FakeContext = {
    fillStyle: "",
    fills: [],
    fillRect: vi.fn((...rect: number[]) => {
      context.fills.push({ color: context.fillStyle, rect });
    }),
  };
  return context;
}

function drawWith(primitive: GammaHeatmapPrimitive, context: FakeContext) {
  const renderer = primitive.paneViews()[0].renderer();
  const target = {
    useBitmapCoordinateSpace: (callback: (scope: unknown) => void) =>
      callback({ context, bitmapSize: { width: 1000, height: 800 }, horizontalPixelRatio: 1, verticalPixelRatio: 1 }),
  };
  renderer?.draw(target as never);
}

// Strike 100 at y=700, 105 at y=600, 110 at y=500; columns at x = 100, 140, 180 (40 px apart).
function attached(timeToX: (time: number) => number | null = (time) => (time - 1000) * 4 + 100) {
  const primitive = new GammaHeatmapPrimitive();
  const requestUpdate = vi.fn();
  primitive.attached({
    series: { priceToCoordinate: (price: number) => 1500 - price * 8 + 0 * price },
    chart: { timeScale: () => ({ timeToCoordinate: timeToX }) },
    requestUpdate,
  } as never);
  return { primitive, requestUpdate };
}

const DATA: HeatmapData = {
  strikes: [100, 105, 110],
  scale: 100,
  columns: [
    { time: 1000, values: [100, 2, -50] },
    { time: 1010, values: [null, -100, 30] },
    { time: 1020, values: [60, 0, null] },
  ],
};

describe("cellColor", () => {
  it("leaves faint, empty or unscaled cells undrawn", () => {
    expect(cellColor(null, 100)).toBeNull();
    expect(cellColor(3, 100)).toBeNull(); // 3% of the scale, below the 4% floor
    expect(cellColor(HEATMAP_MINIMUM_SHARE * 100 - 0.1, 100)).toBeNull();
    expect(cellColor(50, null)).toBeNull();
    expect(cellColor(50, 0)).toBeNull();
  });

  it("gives the values at or above the scale the white end of the ramp, positive green and negative red below it", () => {
    expect(cellColor(100, 100)).toBe("rgba(255, 255, 255, 0.62)");
    expect(cellColor(-250, 100)).toBe("rgba(255, 255, 255, 0.62)");
    const positive = cellColor(40, 100)!;
    const negative = cellColor(-40, 100)!;
    const channels = (color: string) => color.match(/\d+/g)!.slice(0, 3).map(Number);
    expect(channels(positive)[1]).toBeGreaterThan(channels(positive)[0]); // green dominant
    expect(channels(negative)[0]).toBeGreaterThan(channels(negative)[1]); // red dominant
  });
});

describe("rampColor", () => {
  it("returns the first and last stops at 0 and 1 and interpolates in between", () => {
    expect(rampColor(HEATMAP_POSITIVE_STOPS, 0)).toEqual([10, 61, 34]);
    expect(rampColor(HEATMAP_NEGATIVE_STOPS, 1)).toEqual([255, 255, 255]);
    expect(rampColor([[0, 0, 0], [100, 100, 100]], 0.5)).toEqual([50, 50, 50]);
    expect(rampColor([[0, 0, 0], [100, 100, 100]], 5)).toEqual([100, 100, 100]);
  });
});

describe("GammaHeatmapPrimitive", () => {
  it("draws nothing before it is attached or without data", () => {
    const context = fakeContext();
    const primitive = new GammaHeatmapPrimitive();
    primitive.setData(DATA);
    drawWith(primitive, context);
    expect(context.fillRect).not.toHaveBeenCalled();

    const { primitive: attachedPrimitive } = attached();
    drawWith(attachedPrimitive, context);
    expect(context.fillRect).not.toHaveBeenCalled();
  });

  it("draws one rectangle per visible cell at the chart's own coordinates, column width = distance to the next column", () => {
    const { primitive } = attached();
    primitive.setData(DATA);
    const context = fakeContext();

    drawWith(primitive, context);

    // Cells above 4% of the scale (100): col0: 100, (2 is 2%: skipped), -50 -> 2 ; col1: (null), -100, 30 -> 2 ; col2: 60, (0), (null) -> 1.
    expect(context.fills).toHaveLength(5);
    const first = context.fills[0];
    expect(first.color).toBe("rgba(255, 255, 255, 0.62)");
    expect(first.rect[0]).toBe(100); // x of the first column
    expect(first.rect[2]).toBe(40); // distance to the next column
    // Row height: strikes 5 apart = 40 px on screen -> 92% of it.
    expect(first.rect[3]).toBeCloseTo(40 * 0.92);
    // The last column repeats the previous width.
    expect(context.fills.at(-1)!.rect[2]).toBe(40);
  });

  it("skips a column that is off the time scale and a strike that is off the price scale", () => {
    const { primitive } = attached((time) => (time === 1010 ? null : (time - 1000) * 4 + 100));
    primitive.setData(DATA);
    const context = fakeContext();
    drawWith(primitive, context);
    expect(context.fills.map((fill) => fill.rect[0])).not.toContain(140);
  });

  it("asks the chart to repaint when new data arrives and stops after detaching", () => {
    const { primitive, requestUpdate } = attached();
    primitive.setData(DATA);
    expect(requestUpdate).toHaveBeenCalledTimes(1);
    primitive.detached();
    primitive.setData(null);
    expect(requestUpdate).toHaveBeenCalledTimes(1);
    expect(primitive.series).toBeNull();
  });
});
