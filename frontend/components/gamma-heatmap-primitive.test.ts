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
  columnScales,
  bandHeightCssPx,
  heatmapImage,
  mergeSides,
  rampColor,
  sidesRgb,
  strikeGridStep,
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
    // Row height: strikes 5 apart = 40 px on screen, wider than a band may be (18 px): the cell is an 18 px band centred on its strike.
    expect(first.rect[3]).toBe(18);
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
  it("strikeGridStep is the largest step every gap is a whole multiple of, so mixed spacing (NDX) still lands on rows", () => {
    expect(strikeGridStep([100, 105, 110, 115])).toBe(5);
    expect(strikeGridStep([100, 105, 115, 120])).toBe(5); // a missing strike is a multiple of the step
    expect(strikeGridStep([28500, 28505, 28510, 28520, 28530, 28555, 28580])).toBe(5); // steps of 5, 10 and 25
    expect(strikeGridStep([100, 102.5, 105, 110, 120])).toBe(2.5);
    expect(strikeGridStep([100, 100])).toBeNull();
    expect(strikeGridStep([100])).toBeNull();
    expect(strikeGridStep([])).toBeNull();
    // Too many rows: coarsened instead of refusing.
    const coarse = strikeGridStep([0, 0.01, 1000])!;
    expect(coarse).toBeGreaterThan(0.01);
    expect(1000 / coarse + 1).toBeLessThanOrEqual(1500);
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

  it("heatmapImage paints mixed strike spacing as a continuous map: every grid row takes the value of its nearest strike", () => {
    // Strikes 100, 110, 120 (10 apart), 125 and 150 (25 apart): grid step 5, rows 100, 105, ..., 150.
    const data: HeatmapData = { strikes: [100, 110, 120, 125, 150], scale: 100, columns: [{ time: 1000, values: [100, -100, 100, -100, 100] }] };
    const image = heatmapImage(data)!;
    expect(image.height).toBe(11);
    const rowOf = (price: number) => image.height - 1 - (price - 100) / 5;
    const alphaAt = (price: number) => image.pixels[rowOf(price) * image.width * 4 + 3];
    expect(alphaAt(100)).toBeGreaterThan(0);
    expect(alphaAt(105)).toBeGreaterThan(0); // between two strikes: not a blank row
    expect(alphaAt(115)).toBeGreaterThan(0);
    expect(alphaAt(135)).toBeGreaterThan(0); // 10 from 125, 15 from 150: inside the reach (1.5 median gaps)
    expect(alphaAt(150)).toBeGreaterThan(0);
  });

  it("heatmapImage leaves a hole in the strikes empty instead of stretching the last strike over it", () => {
    const data: HeatmapData = { strikes: [100, 105, 110, 200], scale: 100, columns: [{ time: 1000, values: [100, 100, 100, 100] }] };
    const image = heatmapImage(data)!;
    const alphaAt = (price: number) => image.pixels[(image.height - 1 - (price - 100) / 5) * image.width * 4 + 3];
    expect(alphaAt(110)).toBeGreaterThan(0);
    expect(alphaAt(130)).toBe(0); // 20 from the nearest strike, reach is 7.5
    expect(alphaAt(200)).toBeGreaterThan(0);
  });

  it("heatmapImage is null without columns or with fewer than two strikes", () => {
    expect(heatmapImage({ strikes: [100, 105], scale: 1, columns: [] })).toBeNull();
    expect(heatmapImage({ strikes: [100], scale: 1, columns: [{ time: 1000, values: [1] }] })).toBeNull();
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
    const unsigned = channels(cellRgb(40, 100, "unsigned"));
    expect(unsigned[2]).toBeGreaterThan(unsigned[1]); // blue dominant
    expect(unsigned[2]).toBeGreaterThan(unsigned[0]);
    expect(cellRgb(-40, 100, "unsigned")).toEqual(cellRgb(40, 100, "unsigned")); // no sign
    expect(cellRgb(100, 100, "unsigned")).toEqual([255, 255, 255]); // the strongest strike is still white
    const signed = channels(cellRgb(40, 100));
    expect(signed[1]).toBeGreaterThan(signed[2]); // the signed ramp is unchanged: positive stays green
    expect(cellColor(40, 100, "unsigned")).toMatch(/^rgba\(\d+, \d+, \d+, 0\.62\)$/);
  });

  it("heatmapFromResponse picks the ramp of the metric: blue for absolute gamma, purple for vanna, orange for charm, green/red for the rest", () => {
    const response = { schema_version: 1, symbol: "SPY", view: "tactical" as const, bucket_seconds: 60, strikes: [100], columns: [], scale_p98: 1 };
    expect(heatmapFromResponse({ ...response, metric: "absolute_gamma" }).palette).toBe("unsigned");
    expect(heatmapFromResponse({ ...response, metric: "gamma" }).palette).toBe("gamma");
    expect(heatmapFromResponse({ ...response, metric: "delta" }).palette).toBe("gamma");
    expect(heatmapFromResponse({ ...response, metric: "vanna" }).palette).toBe("vanna");
    expect(heatmapFromResponse({ ...response, metric: "charm" }).palette).toBe("charm");
  });
});

describe("vanna (purple) and charm (muted orange) palettes", () => {
  const hue = ([r, g, b]: [number, number, number]) => ({ r, g, b });

  it("vanna is purple when positive (blue and red above green) and plum when negative (red above blue), white at the strongest strike", () => {
    const positive = hue(cellRgb(60, 100, "vanna")!);
    expect(positive.b).toBeGreaterThan(positive.g);
    expect(positive.r).toBeGreaterThan(positive.g);
    const negative = hue(cellRgb(-60, 100, "vanna")!);
    expect(negative.r).toBeGreaterThan(negative.g);
    expect(negative.r).toBeGreaterThanOrEqual(negative.b);
    expect(cellRgb(60, 100, "vanna")).not.toEqual(cellRgb(-60, 100, "vanna")); // the sign still reads
    expect(cellRgb(100, 100, "vanna")).toEqual([255, 255, 255]);
    expect(cellRgb(-100, 100, "vanna")).toEqual([255, 255, 255]);
  });

  it("charm is a MUTED orange: red above green above blue, and no channel as bright as the gamma greens and reds", () => {
    for (const share of [0.3, 0.6, 0.9]) {
      const orange = hue(cellRgb(share * 100, 100, "charm")!);
      expect(orange.r).toBeGreaterThan(orange.g);
      expect(orange.g).toBeGreaterThan(orange.b);
    }
    // Up to the body of the ramp it stays below the full-bright 250 red of the gamma ramps (only the top of the scale fades to white).
    expect(cellRgb(60, 100, "charm")![0]).toBeLessThan(230);
    const ochre = hue(cellRgb(-60, 100, "charm")!);
    expect(ochre.r).toBeGreaterThan(ochre.b);
    expect(cellRgb(60, 100, "charm")).not.toEqual(cellRgb(-60, 100, "charm"));
    expect(cellRgb(100, 100, "charm")).toEqual([255, 255, 255]);
  });

  it("both leave faint cells empty like every other metric", () => {
    expect(cellRgb(3, 100, "vanna")).toBeNull();
    expect(cellRgb(3, 100, "charm")).toBeNull();
    expect(cellRgb(null, 100, "vanna")).toBeNull();
  });
});

describe("sides view (calls and puts, fading to white where both are strong)", () => {
  it("one side alone keeps its own ramp: calls green, puts red", () => {
    const call = sidesRgb(40, null, 100)!;
    expect(call[1]).toBeGreaterThan(call[0]);
    const put = sidesRgb(null, -40, 100)!;
    expect(put[0]).toBeGreaterThan(put[1]);
    expect(sidesRgb(null, null, 100)).toBeNull();
    expect(sidesRgb(3, -2, 100)).toBeNull(); // both too faint to draw
  });

  it("both sides: the stronger side's colour, whitened by the weaker one, white once the weaker reaches 60% of the scale", () => {
    const callOnly = sidesRgb(80, null, 100)!;
    const weakPut = sidesRgb(80, -10, 100)!; // a little put: still green, slightly lighter
    expect(weakPut[1]).toBeGreaterThan(weakPut[0]);
    expect(weakPut[0]).toBeGreaterThanOrEqual(callOnly[0]);
    expect(sidesRgb(80, -60, 100)).toEqual([255, 255, 255]);
    expect(sidesRgb(-70, 70, 100)).toEqual([255, 255, 255]);
    const dimBoth = sidesRgb(15, -15, 100)!; // both weak: stays a dim colour, not white
    expect(Math.min(...dimBoth)).toBeLessThan(200);
  });

  it("never produces amber: no cell of two strong sides has red and green high with blue low", () => {
    for (const call of [20, 40, 60, 80]) {
      for (const put of [-20, -40, -60, -80]) {
        const [r, g, b] = sidesRgb(call, put, 100)!;
        expect(r > 200 && g > 200 && b < 120).toBe(false);
      }
    }
  });

  it("mergeSides gives one matrix with the union of strikes and times, one shared scale and calls in values / puts in puts", () => {
    const calls = { strikes: [100, 105], scale: 6, columns: [{ time: 1, values: [6, 2] }, { time: 2, values: [5, 1] }] };
    const puts = { strikes: [100, 105, 110], scale: 9, columns: [{ time: 1, values: [-1, -9, -4] }, { time: 3, values: [-2, -2, -2] }] };
    const merged = mergeSides(calls, puts)!;
    expect(merged.strikes).toEqual([100, 105, 110]);
    expect(merged.scale).toBe(9);
    expect(merged.columns.map((column) => column.time)).toEqual([1, 2, 3]);
    expect(merged.columns[0].values).toEqual([6, 2, null]);
    expect(merged.columns[0].puts).toEqual([-1, -9, -4]);
    expect(merged.columns[1].puts).toEqual([null, null, null]); // no puts column at that time
    expect(merged.columns[2].values).toEqual([null, null, null]);
    expect(mergeSides(calls, null)).toBe(calls);
    expect(mergeSides(null, puts)).toBe(puts);
    expect(mergeSides(null, null)).toBeNull();
  });

  it("the image of a merged matrix paints a both-strong cell white", () => {
    const merged = mergeSides(
      { strikes: [100, 105], scale: 100, columns: [{ time: 1000, values: [90, 0] }] },
      { strikes: [100, 105], scale: 100, columns: [{ time: 1000, values: [-80, -90] }] },
    )!;
    const image = heatmapImage(merged)!;
    const pixelAt = (price: number) => {
      const offset = ((image.height - 1 - (price - 100) / 5) * image.width) * 4;
      return [image.pixels[offset], image.pixels[offset + 1], image.pixels[offset + 2]];
    };
    expect(pixelAt(100)).toEqual([255, 255, 255]); // strong call and strong put
    const onlyPut = pixelAt(105);
    expect(onlyPut[0]).toBeGreaterThan(onlyPut[1]); // put alone: red
  });
});

describe("column scale (every minute against itself, history stays as painted)", () => {
  const strikes = [100, 105, 110];
  const quiet = { time: 1000, values: [10, 40, 20] };
  const busy = { time: 1060, values: [500, 4000, 2000] };

  it("a column's scale is its own p98; day mode uses the matrix scale", () => {
    const data = { strikes, scale: 4000, scaleMode: "column" as const, columns: [quiet, busy] };
    expect(columnScales(data)).toEqual([40, 4000]);
    expect(columnScales({ ...data, scaleMode: "day" })).toEqual([4000, 4000]);
    expect(columnScales({ strikes, scale: 4000, columns: [quiet] })).toEqual([4000]); // no mode = the old whole-matrix behaviour
    expect(columnScales({ strikes, scale: 1, scaleMode: "column", columns: [{ time: 1, values: [null, null, null] }] })).toEqual([null]);
  });

  it("in column mode the strongest strike of a QUIET minute still reads white, which the day scale leaves dim", () => {
    const column = heatmapImage({ strikes, scale: 4000, scaleMode: "column", columns: [quiet, busy] })!;
    const day = heatmapImage({ strikes, scale: 4000, scaleMode: "day", columns: [quiet, busy] })!;
    const pixel = (image: NonNullable<ReturnType<typeof heatmapImage>>, columnIndex: number, price: number) => {
      const offset = ((image.height - 1 - (price - 100) / 5) * image.width + columnIndex) * 4;
      return [image.pixels[offset], image.pixels[offset + 1], image.pixels[offset + 2], image.pixels[offset + 3]];
    };
    expect(pixel(column, 0, 105).slice(0, 3)).toEqual([255, 255, 255]); // 40 is the top of its own minute
    expect(pixel(day, 0, 105)[3]).toBe(0); // 40 / 4000 = 1%: under the 4% floor, not drawn at all
  });

  it("a column keeps its colours when a later, much bigger column arrives (the history does not move)", () => {
    const before = heatmapImage({ strikes, scale: 40, scaleMode: "column", columns: [quiet] })!;
    const after = heatmapImage({ strikes, scale: 4000, scaleMode: "column", columns: [quiet, busy] })!;
    for (let row = 0; row < before.height; row += 1) {
      const a = (row * before.width) * 4;
      const b = (row * after.width) * 4;
      expect([...after.pixels.slice(b, b + 4)]).toEqual([...before.pixels.slice(a, a + 4)]);
    }
  });

  it("the sides view takes one scale per minute over calls and puts together", () => {
    const merged = mergeSides(
      { strikes, scale: 9000, scaleMode: "column", columns: [{ time: 1000, values: [10, 40, 20] }] },
      { strikes, scale: 9000, scaleMode: "column", columns: [{ time: 1000, values: [-5, -80, -2] }] },
    )!;
    expect(merged.scaleMode).toBe("column");
    expect(columnScales(merged)).toEqual([80]);
  });
});

describe("band height for strikes far apart on screen", () => {
  it("leaves SPX/NDX-like spacing (30 to 35 px between strikes) alone and caps SPY/QQQ/IWM/NVDA-like spacing (60 to 100 px)", () => {
    expect(bandHeightCssPx(null)).toBeNull();
    expect(bandHeightCssPx(Number.NaN)).toBeNull();
    expect(bandHeightCssPx(12)).toBeNull();
    expect(bandHeightCssPx(30)).toBeNull();
    expect(bandHeightCssPx(35)).toBeNull();
    expect(bandHeightCssPx(40)).toBe(18);
    expect(bandHeightCssPx(60)).toBe(18);
    expect(bandHeightCssPx(100)).toBe(18);
  });
});
