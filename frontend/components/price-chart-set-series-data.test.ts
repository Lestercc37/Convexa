import { describe, expect, it, vi } from "vitest";
import type { IChartApi, ISeriesApi } from "lightweight-charts";

import { setSeriesData } from "./price-chart";

type Data = Parameters<typeof setSeriesData>[2];

function fakes(rangeAfterEachCall: Array<{ from: number; to: number } | null>) {
  let calls = 0;
  const setData = vi.fn(() => {
    calls += 1;
  });
  const series = { setData } as unknown as ISeriesApi<"Candlestick">;
  const chart = {
    timeScale: () => ({ getVisibleLogicalRange: () => rangeAfterEachCall[Math.min(calls, rangeAfterEachCall.length) - 1] ?? null }),
  } as unknown as IChartApi;
  return { setData, series, chart };
}

const bars = [{ time: 100 }, { time: 160, open: 1, high: 2, low: 0.5, close: 1.5 }] as unknown as Data;

describe("setSeriesData", () => {
  it("sets the data once when the visible range survives", () => {
    const { setData, series, chart } = fakes([{ from: 0, to: 390 }]);
    setSeriesData(chart, series, bars);
    expect(setData).toHaveBeenCalledTimes(1);
  });

  it("sets the same data a second time when the first call left the visible range null (the blank chart ~30 s after load)", () => {
    const { setData, series, chart } = fakes([null, { from: 0, to: 390 }]);
    setSeriesData(chart, series, bars);
    expect(setData).toHaveBeenCalledTimes(2);
    expect(setData.mock.calls[1]).toEqual(setData.mock.calls[0]);
  });

  it("never loops: a range that stays null costs exactly one extra call", () => {
    const { setData, series, chart } = fakes([null, null, null]);
    setSeriesData(chart, series, bars);
    expect(setData).toHaveBeenCalledTimes(2);
  });

  it("does not look for a range on an empty series or without a chart", () => {
    const empty = fakes([null]);
    setSeriesData(empty.chart, empty.series, [] as unknown as Data);
    expect(empty.setData).toHaveBeenCalledTimes(1);
    const noChart = fakes([null]);
    setSeriesData(null, noChart.series, bars);
    expect(noChart.setData).toHaveBeenCalledTimes(1);
  });
});
