import { describe, expect, it, vi } from "vitest";
import {
  cellColor,
  cellRgb,
  GammaHeatmapPrimitive,
  HEATMAP_ALPHA,
  HEATMAP_MINIMUM_SHARE,
  HEATMAP_NEGATIVE_STOPS,
  HEATMAP_POSITIVE_STOPS,
  heatmapFromResponse,
  heatmapImage,
  rampColor,
  uniformStrikeStep,
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

// Strike 100 at y=700, 105 at y=600, 110 at y=500. Bars every 10 s from t=1000, 40 px apart (x = index * 40 + 100), so the columns at
// 1000 / 1010 / 1020 sit at x = 100, 140, 180. `offGrid` lists times the time scale has no index for at all.
function attached(offGrid: number[] = [], smooth = false) {
  const primitive = new GammaHeatmapPrimitive({ smooth });
  const requestUpdate = vi.fn();
  primitive.attached({
    series: {
      priceToCoordinate: (price: number) => 1500 - price * 8 + 0 * price,
      dataByIndex: (index: number) => (index >= 0 && index < 100 ? { time: 1000 + index * 10 } : null),
    },
    chart: {
      timeScale: () => ({
        timeToIndex: (time: number) => (offGrid.includes(time) ? null : Math.max(0, Math.min(99, Math.round((time - 1000) / 10)))),
        logicalToCoordinate: (index: number) => index * 40 + 100,
      }),
    },
    requestUpdate,
  } as never);
  return { primitive, requestUpdate };
}

function attachedSmooth() {
  return attached([], true);
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
    const { primitive } = attached([1010]);
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

describe("smooth drawing helpers", () => {
  it("uniformStrikeStep returns the grid step, tolerates gaps and refuses strikes that are not on one grid", () => {
    expect(uniformStrikeStep([100, 105, 110, 115])).toBe(5);
    expect(uniformStrikeStep([100, 105, 115, 120])).toBe(5); // a missing strike leaves a blank row
    expect(uniformStrikeStep([100, 102.5, 105, 110, 120])).toBeNull(); // mixed 2.5 / 5 / 10 spacing: sharp cells instead
    expect(uniformStrikeStep([100])).toBeNull();
    expect(uniformStrikeStep([])).toBeNull();
  });

  it("heatmapImage makes one pixel per column and strike row, highest strike first, faint and empty cells transparent", () => {
    const image = heatmapImage(DATA)!;
    expect(image.width).toBe(3);
    expect(image.height).toBe(3); // strikes 100, 105, 110
    expect([image.lowStrike, image.highStrike]).toEqual([100, 110]);
    const pixel = (column: number, strike: number) => {
      const row = image.height - 1 - (strike - 100) / 5;
      const offset = (row * image.width + column) * 4;
      return Array.from(image.pixels.slice(offset, offset + 4));
    };
    expect(pixel(0, 100)).toEqual([255, 255, 255, Math.round(HEATMAP_ALPHA * 255)]); // value 100 = the scale: white end of the ramp
    expect(pixel(0, 105)).toEqual([0, 0, 0, 0]); // 2% of the scale: undrawn
    expect(pixel(1, 100)).toEqual([0, 0, 0, 0]); // null
    expect(pixel(2, 110)[3]).toBe(0); // null
    const negative = pixel(0, 110);
    expect(negative[0]).toBeGreaterThan(negative[1]); // value -50 (half the scale): red side of the ramp
  });

  it("heatmapImage is null without columns or on an irregular strike grid", () => {
    expect(heatmapImage({ strikes: [100, 105], scale: 1, columns: [] })).toBeNull();
    expect(heatmapImage({ strikes: [100, 102.5, 105, 110, 120], scale: 1, columns: [{ time: 1000, values: [1, 1, 1, 1, 1] }] })).toBeNull();
  });

  it("cellRgb and cellColor agree", () => {
    expect(cellRgb(null, 100)).toBeNull();
    expect(cellRgb(100, 100)).toEqual([255, 255, 255]);
    expect(cellColor(100, 100)).toBe(`rgba(255, 255, 255, ${HEATMAP_ALPHA})`);
  });

  it("falls back to the sharp cells when the smooth drawing cannot run (no canvas image support in the test environment)", () => {
    const { primitive } = attachedSmooth();
    primitive.setData(DATA);
    const context = fakeContext();
    drawWith(primitive, context);
    expect(context.fills).toHaveLength(5); // same cells as the sharp drawing
  });
});

describe("unsigned metric (absolute gamma)", () => {
  it("is drawn with the neutral blue ramp, whatever the sign of the stored number, and never the green or red one", () => {
    const channels = (rgb: [number, number, number] | null) => rgb!;
    const unsigned = channels(cellRgb(40, 100, true));
    expect(unsigned[2]).toBeGreaterThan(unsigned[1]); // blue dominant
    expect(unsigned[2]).toBeGreaterThan(unsigned[0]);
    expect(cellRgb(-40, 100, true)).toEqual(cellRgb(40, 100, true)); // no sign
    expect(cellRgb(100, 100, true)).toEqual([255, 255, 255]); // the strongest strike is still white
    const signed = channels(cellRgb(40, 100));
    expect(signed[1]).toBeGreaterThan(signed[2]); // the signed ramp is unchanged: positive stays green
    expect(cellColor(40, 100, true)).toMatch(/^rgba\(\d+, \d+, \d+, 0\.62\)$/);
  });

  it("heatmapFromResponse marks only absolute gamma as unsigned", () => {
    const response = { schema_version: 1, symbol: "SPY", view: "tactical" as const, bucket_seconds: 60, strikes: [100], columns: [], scale_p98: 1 };
    expect(heatmapFromResponse({ ...response, metric: "absolute_gamma" }).unsigned).toBe(true);
    expect(heatmapFromResponse({ ...response, metric: "gamma" }).unsigned).toBe(false);
    expect(heatmapFromResponse({ ...response, metric: "delta" }).unsigned).toBe(false);
  });
});
