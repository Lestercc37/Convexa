import { describe, expect, it, vi } from "vitest";
import {
  GEX_HISTOGRAM_MAX_WIDTH_FRACTION,
  GEX_HISTOGRAM_NEGATIVE_COLOR,
  GEX_HISTOGRAM_POSITIVE_COLOR,
  GexHistogramPrimitive,
  medianRowStep,
  type GexBar,
} from "./gex-histogram-primitive";

type FakeContext = { fillStyle: string; fillRect: ReturnType<typeof vi.fn>; fills: string[] };

function fakeContext(): FakeContext {
  const context: FakeContext = {
    fillStyle: "",
    fills: [],
    fillRect: vi.fn(() => {
      context.fills.push(context.fillStyle);
    }),
  };
  return context;
}

function drawWith(primitive: GexHistogramPrimitive, context: FakeContext, width = 1000, height = 800) {
  const renderer = primitive.paneViews()[0].renderer();
  const target = {
    useBitmapCoordinateSpace: (callback: (scope: unknown) => void) =>
      callback({ context, bitmapSize: { width, height }, horizontalPixelRatio: 1, verticalPixelRatio: 1 }),
  };
  renderer?.draw(target as never);
}

function attachedPrimitive(coordinate: (price: number) => number | null) {
  const primitive = new GexHistogramPrimitive();
  const requestUpdate = vi.fn();
  primitive.attached({ series: { priceToCoordinate: coordinate }, requestUpdate } as never);
  return { primitive, requestUpdate };
}

const BARS: GexBar[] = [
  { strike: 100, net: 5, absolute: 5 },
  { strike: 105, net: -10, absolute: 10 },
  { strike: 110, net: 2.5, absolute: 2.5 },
];

describe("medianRowStep", () => {
  it("is the median distance between adjacent rows, ignoring one far-away outlier", () => {
    expect(medianRowStep([100, 110, 120, 200])).toBe(10);
  });

  it("is null when there is nothing to measure", () => {
    expect(medianRowStep([])).toBeNull();
    expect(medianRowStep([50])).toBeNull();
    expect(medianRowStep([50, 50])).toBeNull();
  });
});

describe("GexHistogramPrimitive", () => {
  it("draws one bar per strike against the right edge, widest = the largest absolute gamma", () => {
    const { primitive } = attachedPrimitive((price) => 800 - price * 2);
    primitive.setBars(BARS);
    const context = fakeContext();

    drawWith(primitive, context);

    expect(context.fillRect).toHaveBeenCalledTimes(3);
    const calls = context.fillRect.mock.calls as [number, number, number, number][];
    for (const [x, , width] of calls) expect(x + width).toBeCloseTo(1000); // right-aligned
    const widths = calls.map(([, , width]) => width);
    expect(Math.max(...widths)).toBeCloseTo(1000 * GEX_HISTOGRAM_MAX_WIDTH_FRACTION);
    expect(widths[0]).toBeCloseTo(110); // 5 of 10
    expect(widths[2]).toBeCloseTo(55); // 2.5 of 10
  });

  it("puts each bar at the vertical position the price scale gives its strike", () => {
    const { primitive } = attachedPrimitive((price) => 800 - price * 2);
    primitive.setBars(BARS);
    const context = fakeContext();

    drawWith(primitive, context);

    const calls = context.fillRect.mock.calls as [number, number, number, number][];
    // strike 100 -> y 600, 105 -> 590, 110 -> 580: the row is centred on y
    const centres = calls.map(([, y, , height]) => y + height / 2);
    expect(centres[0]).toBeCloseTo(600);
    expect(centres[1]).toBeCloseTo(590);
    expect(centres[2]).toBeCloseTo(580);
    // rows are 72% of the 10 px step: they do not touch each other
    expect(calls[0][3]).toBeCloseTo(7.2);
  });

  it("colours positive net gamma green and negative red", () => {
    const { primitive } = attachedPrimitive((price) => 800 - price * 2);
    primitive.setBars(BARS);
    const context = fakeContext();

    drawWith(primitive, context);

    expect(context.fills).toEqual([
      GEX_HISTOGRAM_POSITIVE_COLOR,
      GEX_HISTOGRAM_NEGATIVE_COLOR,
      GEX_HISTOGRAM_POSITIVE_COLOR,
    ]);
  });

  it("skips strikes the price scale cannot place (scrolled out of view)", () => {
    const { primitive } = attachedPrimitive((price) => (price === 105 ? null : 800 - price * 2));
    primitive.setBars(BARS);
    const context = fakeContext();

    drawWith(primitive, context);

    expect(context.fillRect).toHaveBeenCalledTimes(2);
  });

  it("draws nothing without bars, with all-zero gamma, or once detached", () => {
    const { primitive } = attachedPrimitive((price) => 800 - price * 2);
    const context = fakeContext();
    drawWith(primitive, context);
    primitive.setBars([{ strike: 100, net: 0, absolute: 0 }]);
    drawWith(primitive, context);
    primitive.setBars(BARS);
    primitive.detached();
    drawWith(primitive, context);

    expect(context.fillRect).not.toHaveBeenCalled();
  });

  it("asks the chart to repaint when new bars arrive", () => {
    const { primitive, requestUpdate } = attachedPrimitive((price) => price);

    primitive.setBars(BARS);

    expect(requestUpdate).toHaveBeenCalledTimes(1);
  });

  it("sits at the bottom of the pane so candles and lines stay on top", () => {
    const { primitive } = attachedPrimitive((price) => price);
    expect(primitive.paneViews()[0].zOrder?.()).toBe("bottom");
  });
});
