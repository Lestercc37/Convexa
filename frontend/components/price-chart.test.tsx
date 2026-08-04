import { render, screen } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { GammaResponse } from "@/lib/types";
import { PriceChart } from "./price-chart";

const chartMocks = vi.hoisted(() => ({
  createChart: vi.fn(),
  setData: vi.fn(),
  update: vi.fn(),
  createPriceLine: vi.fn(() => ({})),
  removePriceLine: vi.fn(),
  remove: vi.fn(),
}));

vi.mock("lightweight-charts", () => ({
  CandlestickSeries: "CandlestickSeries",
  ColorType: { Solid: "solid" },
  LineStyle: { Dashed: 2 },
  createChart: chartMocks.createChart,
}));

beforeAll(() => {
  chartMocks.createChart.mockReturnValue({
    addSeries: () => ({
      setData: chartMocks.setData,
      update: chartMocks.update,
      createPriceLine: chartMocks.createPriceLine,
      removePriceLine: chartMocks.removePriceLine,
    }),
    applyOptions: vi.fn(),
    timeScale: () => ({ fitContent: vi.fn() }),
    remove: chartMocks.remove,
  });
  vi.stubGlobal(
    "ResizeObserver",
    class ResizeObserver {
      observe() {}
      disconnect() {}
    },
  );
});

beforeEach(() => vi.clearAllMocks());

const gamma: GammaResponse = {
  schema_version: 1,
  symbol: "SPY",
  as_of: "2026-08-03T14:30:00Z",
  gamma_flip: 548.5,
  call_wall: 560,
  put_wall: 540,
  absolute_gamma_strike: 550,
  dealer_position: "long_gamma",
};

describe("PriceChart", () => {
  it("mounts Lightweight Charts with candles and Gamma overlays", () => {
    render(
      <PriceChart
        symbol="SPY"
        gamma={gamma}
        candles={[
          { time: 1_786_026_600, open: 548, high: 552, low: 548, close: 550 },
        ]}
      />,
    );

    expect(screen.getByLabelText("Chart de velas para SPY")).toBeInTheDocument();
    expect(screen.getByText("SPY · Velas de 1 minuto")).toBeInTheDocument();
    expect(chartMocks.createChart).toHaveBeenCalledOnce();
    expect(chartMocks.setData).toHaveBeenCalledWith([
      { time: 1_786_026_600, open: 548, high: 552, low: 548, close: 550 },
    ]);
    expect(chartMocks.update).toHaveBeenCalledWith({
      time: 1_786_026_600,
      open: 548,
      high: 552,
      low: 548,
      close: 550,
    });
    expect(chartMocks.createPriceLine).toHaveBeenCalledTimes(4);
  });

  it("merges Flip and Absolute Gamma overlays inside the 2% threshold", () => {
    render(
      <PriceChart
        symbol="SPY"
        gamma={{ ...gamma, absolute_gamma_strike: 548.7 }}
        candles={[]}
      />,
    );

    expect(chartMocks.createPriceLine).toHaveBeenCalledTimes(3);
    expect(chartMocks.createPriceLine).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Flip / Abs. Gamma", price: 548.5 }),
    );
  });
});
