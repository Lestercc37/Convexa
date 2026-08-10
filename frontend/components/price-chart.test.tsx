import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { AtrRange, GammaResponse } from "@/lib/types";
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
  priceToCoordinate: vi.fn(),
  subscribeVisibleLogicalRangeChange: vi.fn(),
  unsubscribeVisibleLogicalRangeChange: vi.fn(),
  subscribeSizeChange: vi.fn(),
  unsubscribeSizeChange: vi.fn(),
  getGammaHistory: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  getGammaHistory: chartMocks.getGammaHistory,
}));

vi.mock("lightweight-charts", () => ({
  CandlestickSeries: "CandlestickSeries",
  LineSeries: "LineSeries",
  ColorType: { Solid: "solid" },
  LineStyle: { Dashed: 2, Solid: 0 },
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
          priceToCoordinate: chartMocks.priceToCoordinate,
        }
      : { setData: chartMocks.lineSetData },
  );
  chartMocks.createChart.mockReturnValue({
    addSeries: chartMocks.addSeries,
    removeSeries: chartMocks.removeSeries,
    applyOptions: vi.fn(),
    timeScale: () => ({
      fitContent: vi.fn(),
      subscribeVisibleLogicalRangeChange: chartMocks.subscribeVisibleLogicalRangeChange,
      unsubscribeVisibleLogicalRangeChange: chartMocks.unsubscribeVisibleLogicalRangeChange,
      subscribeSizeChange: chartMocks.subscribeSizeChange,
      unsubscribeSizeChange: chartMocks.unsubscribeSizeChange,
    }),
    remove: chartMocks.remove,
  });
});

beforeEach(() => {
  vi.clearAllMocks();
  chartMocks.priceToCoordinate.mockImplementation(() => null);
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
  max_pain: 549,
  net_gamma: 1,
  vega_exposure: 2,
  theta_exposure: 3,
  charm_exposure: 4,
  vanna_exposure: 5,
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

  it("draws the VWAP line and both ATR bands when both are ready", () => {
    chartMocks.priceToCoordinate.mockImplementation((price: number) => 500 - price);
    const readyAtrRange: AtrRange = {
      atr: 20,
      atr_provisional: false,
      daily_bars_count: 15,
      today_open: 500,
      bands_provisional: false,
      outer_upper_band: 520,
      outer_lower_band: 480,
      inner_upper_band: 510,
      inner_lower_band: 490,
    };
    const { container } = render(
      <PriceChart
        symbol="SPY"
        gamma={gamma}
        candles={[]}
        vwapPoints={[
          { timestamp: "2026-08-03T13:30:00Z", value: 549 },
          { timestamp: "2026-08-03T13:31:00Z", value: 550 },
        ]}
        atrRange={readyAtrRange}
      />,
    );

    expect(chartMocks.addSeries).toHaveBeenCalledWith(
      "LineSeries",
      expect.objectContaining({ title: "VWAP Anclado", color: "#f3c969", lineStyle: 0 }),
    );
    expect(chartMocks.lineSetData).toHaveBeenCalledWith([
      { time: 1_785_763_800, value: 549 },
      { time: 1_785_763_860, value: 550 },
    ]);

    const outer = container.querySelector<HTMLElement>(".atr-band-outer");
    const inner = container.querySelector<HTMLElement>(".atr-band-inner");
    expect(outer).not.toBeNull();
    expect(inner).not.toBeNull();
    expect(outer?.style.top).toBe("-20px");
    expect(outer?.style.height).toBe("40px");
    expect(inner?.style.top).toBe("-10px");
    expect(inner?.style.height).toBe("20px");
  });

  it("draws nothing extra when VWAP and ATR are both provisional", () => {
    const provisionalAtrRange: AtrRange = {
      atr: null,
      atr_provisional: true,
      daily_bars_count: 3,
      today_open: null,
      bands_provisional: true,
      outer_upper_band: null,
      outer_lower_band: null,
      inner_upper_band: null,
      inner_lower_band: null,
    };
    const { container } = render(
      <PriceChart symbol="SPY" gamma={gamma} candles={[]} vwapPoints={[]} atrRange={provisionalAtrRange} />,
    );

    expect(chartMocks.addSeries).not.toHaveBeenCalledWith(
      "LineSeries",
      expect.objectContaining({ title: "VWAP Anclado" }),
    );
    expect(container.querySelector(".atr-band-outer")).toBeNull();
    expect(container.querySelector(".atr-band-inner")).toBeNull();
  });

  it("hides ATR bands when the ATR itself is ready but today's open is not", () => {
    const mixedAtrRange: AtrRange = {
      atr: 20,
      atr_provisional: false,
      daily_bars_count: 15,
      today_open: null,
      bands_provisional: true,
      outer_upper_band: null,
      outer_lower_band: null,
      inner_upper_band: null,
      inner_lower_band: null,
    };
    const { container } = render(
      <PriceChart symbol="SPY" gamma={gamma} candles={[]} vwapPoints={[]} atrRange={mixedAtrRange} />,
    );

    expect(container.querySelector(".atr-band-outer")).toBeNull();
    expect(container.querySelector(".atr-band-inner")).toBeNull();
  });

  it("toggles the VWAP and ATR overlays off via their checkboxes", async () => {
    const user = userEvent.setup();
    chartMocks.priceToCoordinate.mockImplementation((price: number) => 500 - price);
    const readyAtrRange: AtrRange = {
      atr: 20,
      atr_provisional: false,
      daily_bars_count: 15,
      today_open: 500,
      bands_provisional: false,
      outer_upper_band: 520,
      outer_lower_band: 480,
      inner_upper_band: 510,
      inner_lower_band: 490,
    };
    const { container } = render(
      <PriceChart
        symbol="SPY"
        gamma={gamma}
        candles={[]}
        vwapPoints={[{ timestamp: "2026-08-03T13:30:00Z", value: 549 }]}
        atrRange={readyAtrRange}
      />,
    );

    expect(container.querySelector(".atr-band-outer")).not.toBeNull();

    await user.click(screen.getByRole("checkbox", { name: "VWAP Anclado" }));
    await user.click(screen.getByRole("checkbox", { name: "Rango ATR" }));

    expect(chartMocks.removeSeries).toHaveBeenCalled();
    expect(container.querySelector(".atr-band-outer")).toBeNull();
    expect(container.querySelector(".atr-band-inner")).toBeNull();
  });

  it("lets the library own resizing via autoSize instead of a hand-rolled ResizeObserver", () => {
    render(<PriceChart symbol="SPY" gamma={gamma} candles={[]} />);

    expect(chartMocks.createChart).toHaveBeenCalledWith(
      expect.any(HTMLElement),
      expect.objectContaining({ autoSize: true }),
    );
  });

  it("keeps ATR bands correctly positioned after the chart's coordinate system changes (resize or pan)", () => {
    let currentPrice = 500;
    chartMocks.priceToCoordinate.mockImplementation((price: number) => currentPrice - price);
    const readyAtrRange: AtrRange = {
      atr: 20,
      atr_provisional: false,
      daily_bars_count: 15,
      today_open: 500,
      bands_provisional: false,
      outer_upper_band: 520,
      outer_lower_band: 480,
      inner_upper_band: 510,
      inner_lower_band: 490,
    };
    const { container } = render(
      <PriceChart symbol="SPY" gamma={gamma} candles={[]} atrRange={readyAtrRange} />,
    );

    const topBefore = container.querySelector<HTMLElement>(".atr-band-outer")?.style.top;
    expect(topBefore).toBe("-20px");

    // Simulate the chart's coordinate system changing — the same recompute
    // path a real pan drives via subscribeVisibleLogicalRangeChange, here
    // triggered through subscribeSizeChange (e.g. the container's flex
    // layout settling, or the window resizing) to prove bands don't go
    // stale regardless of which event caused the change.
    currentPrice = 600;
    const sizeChangeHandler = chartMocks.subscribeSizeChange.mock.calls.at(-1)?.[0];
    expect(sizeChangeHandler).toBeTypeOf("function");
    act(() => sizeChangeHandler());

    const topAfter = container.querySelector<HTMLElement>(".atr-band-outer")?.style.top;
    expect(topAfter).toBe("80px");
    expect(topAfter).not.toBe(topBefore);
  });
});
