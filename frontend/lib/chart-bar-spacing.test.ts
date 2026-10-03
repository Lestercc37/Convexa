import { describe, expect, it, vi } from "vitest";
import type { IChartApi } from "lightweight-charts";
import { fitContentCappingBarSpacing, MAX_BAR_SPACING_PX } from "./chart-bar-spacing";

function fakeChart(width: number) {
  const scale = {
    width: () => width,
    fitContent: vi.fn(),
    setVisibleLogicalRange: vi.fn(),
  };
  return { scale, chart: { timeScale: () => scale } as unknown as IChartApi };
}

describe("fitContentCappingBarSpacing", () => {
  it("fits normally when every bar already fits at or under the cap (1m)", () => {
    const { chart, scale } = fakeChart(1400);
    fitContentCappingBarSpacing(chart, 390); // 391 bars -> 3.58 px each
    expect(scale.fitContent).toHaveBeenCalledOnce();
    expect(scale.setVisibleLogicalRange).not.toHaveBeenCalled();
  });

  it("widens the visible range so bars are exactly the cap when fitting would stretch them (5m)", () => {
    const { chart, scale } = fakeChart(1400);
    fitContentCappingBarSpacing(chart, 79); // 80 bars -> 17.5 px each if fitted
    expect(scale.fitContent).not.toHaveBeenCalled();
    const [{ from, to }] = scale.setVisibleLogicalRange.mock.calls[0];
    expect(from).toBe(0);
    expect(1400 / (to - from + 1)).toBeCloseTo(MAX_BAR_SPACING_PX, 6);
  });

  it("uses the width it is given instead of the chart's own when passed one", () => {
    const { chart, scale } = fakeChart(1400);
    fitContentCappingBarSpacing(chart, 26, 600);
    const [{ from, to }] = scale.setVisibleLogicalRange.mock.calls[0];
    expect(600 / (to - from + 1)).toBeCloseTo(MAX_BAR_SPACING_PX, 6);
  });

  it("falls back to fitContent with no width or no bars yet", () => {
    const empty = fakeChart(1400);
    fitContentCappingBarSpacing(empty.chart, -1);
    expect(empty.scale.fitContent).toHaveBeenCalledOnce();
    const noWidth = fakeChart(0);
    fitContentCappingBarSpacing(noWidth.chart, 79);
    expect(noWidth.scale.fitContent).toHaveBeenCalledOnce();
  });
});
