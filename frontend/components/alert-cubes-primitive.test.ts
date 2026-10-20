import { describe, expect, it, vi } from "vitest";
import type { AlertCube } from "@/lib/alert-cubes";
import { AlertCubesPrimitive, CUBE_COLORS } from "./alert-cubes-primitive";

type FakeContext = {
  font: string;
  textBaseline: string;
  lineJoin: string;
  fillText: ReturnType<typeof vi.fn>;
  strokeText: ReturnType<typeof vi.fn>;
  measureText: (text: string) => { width: number };
  fillStyle: string;
  strokeStyle: string;
  lineWidth: number;
  globalAlpha: number;
  fillRect: ReturnType<typeof vi.fn>;
  strokeRect: ReturnType<typeof vi.fn>;
  save: () => void;
  restore: () => void;
  fills: { color: string; alpha: number; rect: number[] }[];
};

function fakeContext(): FakeContext {
  const context: FakeContext = {
    font: "",
    textBaseline: "",
    lineJoin: "",
    fillText: vi.fn(),
    strokeText: vi.fn(),
    measureText: (text: string) => ({ width: text.length * 6 }),
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
    globalAlpha: 1,
    fills: [],
    save: () => {},
    restore: () => {},
    strokeRect: vi.fn(),
    fillRect: vi.fn((...rect: number[]) => {
      context.fills.push({ color: context.fillStyle, alpha: context.globalAlpha, rect });
    }),
  };
  return context;
}

function draw(primitive: AlertCubesPrimitive, context: FakeContext, paneHeight = 400) {
  const renderer = primitive.paneViews()[0].renderer();
  const target = {
    useBitmapCoordinateSpace: (callback: (scope: unknown) => void) =>
      callback({
        context,
        bitmapSize: { width: 1000, height: paneHeight },
        mediaSize: { width: 1000, height: paneHeight },
        horizontalPixelRatio: 1,
        verticalPixelRatio: 1,
      }),
  };
  renderer?.draw(target as never);
}

// Bars every 60 s from t=1000 (x = (time - 1000) / 60 * 20 + 100, so a bar is 20 px wide); strike -> y = 800 - strike (strike 500 is y 300).
// `offGrid` lists times for which the time scale has no bar at all (a null from timeToIndex).
function attached(offGrid: number[] = []) {
  const primitive = new AlertCubesPrimitive();
  const requestUpdate = vi.fn();
  const barTime = (index: number) => 1000 + index * 60;
  primitive.attached({
    series: {
      priceToCoordinate: (price: number) => 800 - price,
      dataByIndex: (index: number) => (index >= 0 && index < 100 ? { time: barTime(index) } : null),
    },
    chart: {
      timeScale: () => ({
        timeToIndex: (time: number) => (offGrid.includes(time) ? null : Math.max(0, Math.min(99, Math.round((time - 1000) / 60)))),
        logicalToCoordinate: (index: number) => index * 20 + 100,
      }),
    },
    requestUpdate,
  } as never);
  return { primitive, requestUpdate };
}

function cube(overrides: Partial<AlertCube> & { id: string }): AlertCube {
  return {
    time: 1000,
    strike: 500,
    right: "C",
    deq: 450,
    size: 9,
    side: "buy",
    zone: "ATM",
    alert: {} as never,
    ...overrides,
  };
}

describe("AlertCubesPrimitive", () => {
  it("draws one square per cube, colored by side, centered on the chart's coordinates", () => {
    const { primitive } = attached();
    primitive.setCubes([cube({ id: "a", side: "buy" }), cube({ id: "b", side: "sell", time: 1060, strike: 520 }), cube({ id: "c", side: "mixed", time: 1120, strike: 540 })]);
    const context = fakeContext();

    draw(primitive, context);

    expect(context.fills.map((fill) => fill.color)).toEqual([CUBE_COLORS.buy, CUBE_COLORS.sell, CUBE_COLORS.mixed]);
    expect(context.fills[0].rect).toEqual([Math.round(100 - 4.5), Math.round(300 - 4.5), 9, 9]);
    expect(context.fills.every((fill) => fill.alpha === 1)).toBe(true);
  });

  it("pins a strike above or below the visible range to the pane edge, fainter", () => {
    const { primitive } = attached();
    primitive.setCubes([cube({ id: "high", strike: 7000 }), cube({ id: "low", strike: -200, time: 1060 })]); // y = -6200 and 1000
    const context = fakeContext();

    draw(primitive, context, 400);

    expect(context.fills.map((fill) => fill.alpha)).toEqual([0.45, 0.45]);
    const [high, low] = primitive.placed;
    expect(high.y).toBe(8);
    expect(low.y).toBe(392);
  });

  it("lays cubes that land on the same spot side by side", () => {
    const { primitive } = attached();
    primitive.setCubes([cube({ id: "a" }), cube({ id: "b" }), cube({ id: "c" })]);
    draw(primitive, fakeContext());
    expect(primitive.placed.map((placed) => placed.x)).toEqual([100, 111, 122]); // 9 px + 2 px gap
  });

  it("places a cube between two bars in proportion to its time (an alert at 14:43 on 5-minute candles is not a bar)", () => {
    const { primitive } = attached();
    // 1000 is a bar (x 100); 1075 is 15 s after the bar at 1060 (x 120) and 45 s before the one at 1120 (x 140): 25% of the way = x 125.
    primitive.setCubes([cube({ id: "on-bar", time: 1000 }), cube({ id: "between", time: 1075, strike: 520 })]);
    draw(primitive, fakeContext());
    expect(primitive.placed.map((placed) => placed.x)).toEqual([100, 125]);
  });

  it("skips a cube whose time is off the time scale", () => {
    const { primitive } = attached([1010]);
    primitive.setCubes([cube({ id: "a" }), cube({ id: "off", time: 1010 })]);
    draw(primitive, fakeContext());
    expect(primitive.placed.map((placed) => placed.id)).toEqual(["a"]);
  });

  it("answers hitTest from where the last draw put the cubes, latest on top", () => {
    const { primitive } = attached();
    primitive.setCubes([cube({ id: "a" }), cube({ id: "b" })]);
    draw(primitive, fakeContext());

    expect(primitive.hitTest(100, 300)).toEqual({ cursorStyle: "pointer", externalId: "a", zOrder: "top" });
    expect(primitive.hitTest(111, 300)).toMatchObject({ externalId: "b" });
    expect(primitive.hitTest(300, 100)).toBeNull();
  });

  it("asks for a repaint when cubes or the hovered cube change, and forgets everything when detached", () => {
    const { primitive, requestUpdate } = attached();
    primitive.setCubes([cube({ id: "a" })]);
    primitive.setHovered("a");
    primitive.setHovered("a");
    expect(requestUpdate).toHaveBeenCalledTimes(2);
    primitive.detached();
    expect(primitive.series).toBeNull();
    expect(primitive.placed).toEqual([]);
  });

  it("draws the trade time (ET, HH:MM) to the right of each cube", () => {
    const { primitive } = attached();
    primitive.setCubes([cube({ id: "a" })]); // epoch 1000 s = 19:16 ET the evening before
    const context = fakeContext();
    draw(primitive, context);
    expect(context.fillText).toHaveBeenCalledTimes(1);
    const [text, x, y] = context.fillText.mock.calls[0];
    expect(text).toBe("19:16");
    expect(x).toBe(100 + 9 / 2 + 3);
    expect(y).toBe(300);
  });

  it("skips a label that would sit on top of another one or leave the pane", () => {
    const { primitive } = attached();
    primitive.setCubes([cube({ id: "a" }), cube({ id: "b" })]); // same spot: the second cube is laid beside the first and its label would overlap
    const context = fakeContext();
    draw(primitive, context);
    expect(context.fillText).toHaveBeenCalledTimes(1);

    const edge = attached();
    edge.primitive.setCubes([cube({ id: "far", time: 1000 + 60 * 49 })]); // x = 100 + 49 * 20 = 1080, outside the 1000 px pane
    const edgeContext = fakeContext();
    draw(edge.primitive, edgeContext);
    expect(edgeContext.fillText).not.toHaveBeenCalled();
  });
});
