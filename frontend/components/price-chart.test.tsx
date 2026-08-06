import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { GammaResponse } from "@/lib/types";
import { derivedMetricsFixture } from "@/test/fixtures";
import { PriceChart } from "./price-chart";

const chartMocks = vi.hoisted(() => ({
  createChart: vi.fn(),
  addSeries: vi.fn(),
  setData: vi.fn(),
  lineSetData: vi.fn(),
  update: vi.fn(),
  createPriceLine: vi.fn(() => ({})),
  removePriceLine: vi.fn(),
  removeSeries: vi.fn(),
  remove: vi.fn(),
  getGammaHistory: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  getGammaHistory: chartMocks.getGammaHistory,
}));

vi.mock("lightweight-charts", () => ({
  CandlestickSeries: "CandlestickSeries",
  LineSeries: "LineSeries",
  ColorType: { Solid: "solid" },
  LineStyle: { Dashed: 2 },
  createChart: chartMocks.createChart,
}));

beforeAll(() => {
  chartMocks.addSeries.mockImplementation((definition: string) =>
    definition === "CandlestickSeries"
      ? {
          setData: chartMocks.setData,
          update: chartMocks.update,
          createPriceLine: chartMocks.createPriceLine,
          removePriceLine: chartMocks.removePriceLine,
        }
      : { setData: chartMocks.lineSetData },
  );
  chartMocks.createChart.mockReturnValue({
    addSeries: chartMocks.addSeries,
    removeSeries: chartMocks.removeSeries,
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

beforeEach(() => {
  vi.clearAllMocks();
  chartMocks.getGammaHistory.mockResolvedValue({
    schema_version: 1,
    symbol: "SPY",
    items: [
      {
        schema_version: 1,
        symbol: "SPY",
        as_of: "2026-08-03T14:30:00Z",
        gamma_flip: 548.5,
        call_wall: 560,
        put_wall: 540,
        absolute_gamma_strike: 550,
        max_pain: 549,
        net_gamma: 1,
        vega_exposure: 2,
        theta_exposure: 3,
        charm_exposure: 4,
        vanna_exposure: 5,
        dealer_position: "long_gamma",
      },
    ],
  });
});

const gamma: GammaResponse = {
  schema_version: 1,
  symbol: "SPY",
  as_of: "2026-08-03T14:30:00Z",
  gamma_flip: 548.5,
  call_wall: 560,
  put_wall: 540,
  absolute_gamma_strike: 550,
  dealer_position: "long_gamma",
  derived_metrics: derivedMetricsFixture,
};

describe("PriceChart", () => {
  it("mounts Lightweight Charts with candles and Gamma overlays", () => {
    const { container } = render(
      <PriceChart
        symbol="SPY"
        gamma={gamma}
        candles={[
          { time: 1_786_026_600, open: 548, high: 552, low: 548, close: 550 },
        ]}
      />,
    );

    expect(screen.getByLabelText("Chart de velas para SPY")).toBeInTheDocument();
    expect(container.querySelector('img[src*="logo-watermark.png"]')).toBeInTheDocument();
    expect(screen.getByText("SPY · Velas de 1 minuto")).toBeInTheDocument();
    expect(chartMocks.createChart).toHaveBeenCalledOnce();
    expect(chartMocks.createChart).toHaveBeenCalledWith(
      expect.any(HTMLElement),
      expect.objectContaining({
        layout: expect.objectContaining({ attributionLogo: false }),
      }),
    );
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

  it("switches to three historical level series without Absolute Gamma", async () => {
    const user = userEvent.setup();
    render(<PriceChart symbol="SPY" gamma={gamma} candles={[]} />);

    expect(screen.getByRole("button", { name: "Estático" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByRole("button", { name: "Histórico" }));

    expect(screen.getByRole("button", { name: "Histórico" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await waitFor(() =>
      expect(chartMocks.getGammaHistory).toHaveBeenCalledWith(
        "SPY",
        expect.any(AbortSignal),
      ),
    );
    await waitFor(() => expect(chartMocks.lineSetData).toHaveBeenCalledTimes(3));

    const historicalOptions = chartMocks.addSeries.mock.calls
      .filter(([definition]) => definition === "LineSeries")
      .slice(-3)
      .map(([, options]) => options);
    expect(historicalOptions).toEqual([
      expect.objectContaining({ title: "Call Wall" }),
      expect.objectContaining({ title: "Gamma Flip" }),
      expect.objectContaining({ title: "Put Wall" }),
    ]);
    expect(historicalOptions).not.toContainEqual(
      expect.objectContaining({ title: expect.stringContaining("Abs") }),
    );
    expect(chartMocks.lineSetData).toHaveBeenLastCalledWith([
      { time: 1_785_767_400, value: 540 },
    ]);
  });
});
