import { describe, expect, it, vi } from "vitest";
import {
  barThickness,
  smileDotX,
  PROFILE_GUTTER_CSS_PX,
  AHORA_LINE_COLOR,
  LiveProfilePrimitive,
  profileBarsFromHeatmap,
  profileScale,
  sideBarsFromHeatmaps,
  profileWidthCssPx,
  type ProfileBar,
} from "./live-profile-primitive";

type Op = { op: string; args: unknown[]; fillStyle?: string; strokeStyle?: string };

function fakeContext() {
  const ops: Op[] = [];
  const context: Record<string, unknown> = { fillStyle: "", strokeStyle: "", lineWidth: 1, font: "", textAlign: "", textBaseline: "" };
  for (const name of ["fillRect", "fillText", "beginPath", "moveTo", "lineTo", "stroke", "arc", "fill", "save", "restore", "setLineDash", "roundRect"]) {
    context[name] = vi.fn((...args: unknown[]) => {
      ops.push({ op: name, args, fillStyle: context.fillStyle as string, strokeStyle: context.strokeStyle as string });
    });
  }
  return { context, ops };
}

function drawView(primitive: LiveProfilePrimitive, index: number, context: unknown, width: number, height = 800, ratio = 1) {
  const renderer = primitive.paneViews()[index].renderer();
  const target = {
    useBitmapCoordinateSpace: (callback: (scope: unknown) => void) =>
      callback({ context, bitmapSize: { width, height }, horizontalPixelRatio: ratio, verticalPixelRatio: ratio }),
  };
  renderer?.draw(target as never);
}

function attached(coordinate: (price: number) => number | null = (price) => 1500 - price * 8) {
  const primitive = new LiveProfilePrimitive();
  const requestUpdate = vi.fn();
  primitive.attached({ series: { priceToCoordinate: coordinate }, requestUpdate } as never);
  return { primitive, requestUpdate };
}

// 1000 px pane -> a 200 px column (a fifth), so the line is at x = 800.
const WIDTH = 1000;
const BARS: ProfileBar[] = [
  { strike: 100, net: 50, absolute: 50 },
  { strike: 105, net: -100, absolute: 100 },
  { strike: 110, net: 25, absolute: 25 },
];

describe("profileWidthCssPx", () => {
  it("is a fifth of the pane, between 150 and 320 px", () => {
    expect(profileWidthCssPx(1000)).toBe(200);
    expect(profileWidthCssPx(700)).toBe(150);
    expect(profileWidthCssPx(2400)).toBe(320);
  });

  it("is zero on a pane too narrow to hold the history and the column", () => {
    expect(profileWidthCssPx(519)).toBe(110); // narrow windows keep a 110 px column (2026-10-30)
    expect(profileWidthCssPx(600)).toBe(120);
    expect(profileWidthCssPx(359)).toBe(0);
    expect(profileWidthCssPx(0)).toBe(0);
    expect(profileWidthCssPx(Number.NaN)).toBe(0);
  });
});

describe("profileBarsFromHeatmap", () => {
  it("takes, per strike, the latest value of the recent columns and skips strikes with none", () => {
    const bars = profileBarsFromHeatmap({
      strikes: [100, 105, 110],
      scale: 1,
      columns: [
        { time: 1, values: [1, 2, null] },
        { time: 2, values: [3, null, null] },
      ],
    });
    expect(bars).toEqual([
      { strike: 100, net: 3, absolute: 3 },
      { strike: 105, net: 2, absolute: 2 },
    ]);
  });

  it("keeps the sign in net and the magnitude in absolute, and gives nothing for an empty matrix", () => {
    expect(profileBarsFromHeatmap({ strikes: [100], scale: 1, columns: [{ time: 1, values: [-4] }] })).toEqual([{ strike: 100, net: -4, absolute: 4 }]);
    expect(profileBarsFromHeatmap({ strikes: [100], scale: 1, columns: [] })).toEqual([]);
  });
});

describe("profileScale", () => {
  it("is the p98 of the magnitudes, null with nothing to scale", () => {
    expect(profileScale([])).toBeNull();
    expect(profileScale([{ strike: 1, net: 0, absolute: 0 }])).toBeNull();
    expect(profileScale(BARS)).toBe(100);
  });
});

describe("LiveProfilePrimitive bars", () => {
  it("draws one bar per strike growing from the price axis toward the candles, longest = the largest |value| (square-root scale), never past the pane", () => {
    const { primitive } = attached();
    primitive.setBars(BARS);
    const { context, ops } = fakeContext();
    drawView(primitive, 0, context, WIDTH);

    const rects = ops.filter((entry) => entry.op === "fillRect").map((entry) => entry.args as number[]);
    expect(rects).toHaveLength(3);
    for (const [x, , width] of rects) {
      expect(x + width).toBe(WIDTH - 4); // the flat side is stuck to the price axis (right edge, 4 px inset)
      expect(x).toBeGreaterThanOrEqual(800 + 14); // the peaks never come closer to the AHORA line than the 14 px gutter
    }
    const widths = rects.map((rect) => rect[2]);
    expect(widths[1]).toBeCloseTo(182); // 200 - 14 gutter - 4 inset
    expect(widths[0]).toBeCloseTo(182 * Math.sqrt(0.5)); // square-root scale: half the value is ~71% of the length
    expect(widths[2]).toBeCloseTo(91);
  });

  it("colours the strongest strike white and keeps even a tiny bar visible", () => {
    const { primitive } = attached();
    primitive.setBars([...BARS, { strike: 115, net: 3, absolute: 3 }]);
    const { context, ops } = fakeContext();
    drawView(primitive, 0, context, WIDTH);
    const fills = ops.filter((entry) => entry.op === "fillRect");
    expect(fills[1].fillStyle).toBe("rgb(255, 255, 255)");
    expect(fills).toHaveLength(4); // the 3% bar is drawn too (the heatmap's 4% floor does not apply to a profile)
  });

  it("draws nothing on a narrow pane or with no bars", () => {
    const { primitive } = attached();
    primitive.setBars(BARS);
    const narrow = fakeContext();
    drawView(primitive, 0, narrow.context, 300);
    expect(narrow.ops).toHaveLength(0);
    const empty = attached();
    const none = fakeContext();
    drawView(empty.primitive, 0, none.context, WIDTH);
    expect(none.ops).toHaveLength(0);
  });

  it("skips strikes the chart cannot place and strikes scrolled far out of view", () => {
    const { primitive } = attached((price) => (price === 100 ? null : price === 105 ? -5000 : 300));
    primitive.setBars(BARS);
    const { context, ops } = fakeContext();
    drawView(primitive, 0, context, WIDTH);
    expect(ops.filter((entry) => entry.op === "fillRect")).toHaveLength(1);
  });

  it("uses the ramp of its palette (purple for vanna)", () => {
    const { primitive } = attached();
    primitive.setBars([{ strike: 100, net: 40, absolute: 40 }, { strike: 105, net: 100, absolute: 100 }], "vanna", "VANNA");
    const { context, ops } = fakeContext();
    drawView(primitive, 0, context, WIDTH);
    const [r, g, b] = (ops.filter((entry) => entry.op === "fillRect")[0].fillStyle as string).match(/\d+/g)!.map(Number);
    expect(b).toBeGreaterThan(g);
    expect(r).toBeGreaterThan(g);
  });

  it("asks the chart to repaint when the bars, the smile or the palette change", () => {
    const { primitive, requestUpdate } = attached();
    primitive.setBars(BARS);
    primitive.setSmile([]);
    expect(requestUpdate).toHaveBeenCalledTimes(2);
  });
});

describe("LiveProfilePrimitive overlay (line of time and smile)", () => {
  it("draws the dashed AHORA line where the column starts, with its tag", () => {
    const { primitive } = attached();
    const { context, ops } = fakeContext();
    drawView(primitive, 1, context, WIDTH);

    const move = ops.find((entry) => entry.op === "moveTo")!;
    const line = ops.find((entry) => entry.op === "lineTo")!;
    expect(move.args).toEqual([800, 0]);
    expect(line.args).toEqual([800, 800]);
    expect(ops.find((entry) => entry.op === "stroke")!.strokeStyle).toBe(AHORA_LINE_COLOR);
    expect(ops.find((entry) => entry.op === "setLineDash")!.args[0]).toHaveLength(2);
    expect(ops.some((entry) => entry.op === "fillText" && entry.args[0] === "AHORA")).toBe(true);
  });

  it("draws one dot per smile point INSIDE the column (right of the line and its gutter), calls green and puts red, IV growing to the right", () => {
    const { primitive } = attached();
    primitive.setSmile([
      { strike: 100, iv: 0.2, type: "call" },
      { strike: 105, iv: 0.3, type: "put" },
      { strike: 110, iv: 0.25, type: "call" },
    ]);
    const { context, ops } = fakeContext();
    drawView(primitive, 1, context, WIDTH);

    const dots = ops.filter((entry) => entry.op === "arc");
    expect(dots).toHaveLength(3);
    const xs = dots.map((dot) => dot.args[0] as number);
    for (const x of xs) {
      expect(x).toBeGreaterThanOrEqual(800 + 14);
      expect(x).toBeLessThanOrEqual(WIDTH - 4);
    }
    expect(xs[1]).toBeGreaterThan(xs[2]); // 30% sits further right than 25% (IV grows to the right, like the rest of Convexa's smile)
    expect(xs[2]).toBeGreaterThan(xs[0]); // and 25% closer than 20%
    const fillOps = ops.filter((entry) => entry.op === "fill");
    expect(fillOps.map((entry) => entry.fillStyle)).toEqual(
      expect.arrayContaining([expect.stringContaining("0, 220, 90"), expect.stringContaining("255, 92, 96")]),
    );
  });

  it("draws neither line nor dots on a pane too narrow for the column", () => {
    const { primitive } = attached();
    primitive.setSmile([{ strike: 100, iv: 0.2, type: "call" }]);
    const { context, ops } = fakeContext();
    drawView(primitive, 1, context, 300);
    expect(ops).toHaveLength(0);
  });

  it("falls back to a plain rectangle for the tag when the canvas has no roundRect", () => {
    const { primitive } = attached();
    const { context, ops } = fakeContext();
    delete context.roundRect;
    drawView(primitive, 1, context, WIDTH);
    expect(ops.some((entry) => entry.op === "fillRect")).toBe(true);
  });
});

describe("sides view (calls and puts apart)", () => {
  const calls = { strikes: [100, 105], scale: 1, columns: [{ time: 1, values: [6, 2] }] };
  const puts = { strikes: [100, 105, 110], scale: 1, columns: [{ time: 1, values: [-1, -9, -4] }] };

  it("merges the latest call and put values per strike, and leaves an off side undefined", () => {
    expect(sideBarsFromHeatmaps(calls, puts)).toEqual([
      { strike: 100, net: 5, absolute: 7, call: 6, put: -1 },
      { strike: 105, net: -7, absolute: 11, call: 2, put: -9 },
      { strike: 110, net: -4, absolute: 4, call: undefined, put: -4 },
    ]);
    expect(sideBarsFromHeatmaps(calls, null).every((bar) => bar.put === undefined)).toBe(true);
    expect(sideBarsFromHeatmaps(null, null)).toEqual([]);
  });

  it("draws a green bar over a red bar in each strike's row when both sides are on, longest = the largest |value| of either side", () => {
    const { primitive } = attached();
    primitive.setBars(sideBarsFromHeatmaps(calls, puts));
    const { context, ops } = fakeContext();
    drawView(primitive, 0, context, WIDTH);
    const fills = ops.filter((entry) => entry.op === "fillRect");
    expect(fills).toHaveLength(5); // strikes 100 and 105 have a call and a put, 110 only a put
    const colours = fills.map((entry) => (entry.fillStyle as string).match(/\d+/g)!.map(Number));
    const greens = colours.filter(([r, g]) => g > r);
    const reds = colours.filter(([r, g]) => r > g);
    const whites = colours.filter(([r, g, b]) => r === 255 && g === 255 && b === 255);
    expect(greens.length).toBe(2);
    expect(reds.length).toBe(2);
    expect(whites.length).toBe(1); // the strongest value of either side (the 105 put) reads white
  });

  it("uses the whole row when only one side is on", () => {
    const both = attached();
    both.primitive.setBars(sideBarsFromHeatmaps(calls, puts));
    const only = attached();
    only.primitive.setBars(sideBarsFromHeatmaps(calls, null));
    const a = fakeContext();
    const b = fakeContext();
    drawView(both.primitive, 0, a.context, WIDTH);
    drawView(only.primitive, 0, b.context, WIDTH);
    const heightBoth = (a.ops.find((entry) => entry.op === "fillRect")!.args as number[])[3];
    const heightOnly = (b.ops.find((entry) => entry.op === "fillRect")!.args as number[])[3];
    expect(heightOnly).toBeGreaterThan(heightBoth);
  });
});

describe("profile bar thickness", () => {
  it("is the strike spacing times 0.78, never above 18 CSS px, and at least one pixel", () => {
    expect(barThickness(null, 1)).toBeCloseTo(6 * 0.78);
    expect(barThickness(20, 1)).toBeCloseTo(15.6);
    expect(barThickness(60, 1)).toBe(18); // SPY: strikes 60 px apart used to give a 47 px slab
    expect(barThickness(100, 1)).toBe(18);
    expect(barThickness(120, 2)).toBeCloseTo(36); // bitmap pixels at a pixel ratio of 2: 18 CSS px
    expect(barThickness(0.5, 1)).toBe(1);
  });
});

describe("smile inside the column", () => {
  it("smileDotX puts the lowest IV just past the gutter after the AHORA line and the highest at the right edge, a flat smile in the middle", () => {
    // pane 1000, column 200: line at 800
    const low = smileDotX(0.2, 0.2, 0.3, 1000, 200);
    const high = smileDotX(0.3, 0.2, 0.3, 1000, 200);
    expect(low).toBeGreaterThan(800 + PROFILE_GUTTER_CSS_PX);
    expect(low).toBeLessThan(820);
    expect(high).toBeLessThanOrEqual(1000 - 4);
    expect(high).toBeGreaterThan(990);
    expect(smileDotX(0.25, 0.2, 0.3, 1000, 200)).toBeCloseTo((low + high) / 2);
    expect(smileDotX(0.25, 0.25, 0.25, 1000, 200)).toBeCloseTo((low + high) / 2);
  });

  it("hitSmile finds the dot under the pointer (same placement as the drawing), the nearest one, and none away from every dot", () => {
    const { primitive } = attached();
    primitive.setSmile([
      { strike: 100, iv: 0.2, type: "call" },
      { strike: 105, iv: 0.3, type: "put" },
    ]);
    const { context } = fakeContext();
    drawView(primitive, 1, context, WIDTH); // records the pane width the hit test needs
    const dotX = (iv: number) => smileDotX(iv, 0.2, 0.3, WIDTH, 200);
    // series.priceToCoordinate in these tests is 1500 - price * 8
    expect(primitive.hitSmile(dotX(0.2), 1500 - 100 * 8)).toEqual({ strike: 100, iv: 0.2, type: "call" });
    expect(primitive.hitSmile(dotX(0.3) + 3, 1500 - 105 * 8 - 2)).toEqual({ strike: 105, iv: 0.3, type: "put" });
    expect(primitive.hitSmile(dotX(0.25), 1500 - 102 * 8)).toBeNull();
    expect(primitive.hitSmile(100, 1500 - 100 * 8)).toBeNull(); // over the candles, far from any dot
  });

  it("hitSmile is null before the first draw and with no smile", () => {
    const { primitive } = attached();
    expect(primitive.hitSmile(900, 700)).toBeNull();
    primitive.setSmile([{ strike: 100, iv: 0.2, type: "call" }]);
    expect(primitive.hitSmile(900, 700)).toBeNull(); // pane width unknown until it is drawn
  });
});
