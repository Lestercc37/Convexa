import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { MinuteCandle } from "@/lib/candles";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
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

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, getGammaHistory: chartMocks.getGammaHistory };
});

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

// ATR bands are only meaningful (and only safe to position via
// priceToCoordinate) once the chart has real price variance to auto-scale
// against — see the regression test below for what happens without it.
const candlesWithRange: MinuteCandle[] = [
  { time: 1_785_763_800, open: 495, high: 505, low: 490, close: 500 },
  { time: 1_785_763_860, open: 500, high: 510, low: 498, close: 505 },
];

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
    const { container } = renderWithLanguage(
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
    renderWithLanguage(
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
    renderWithLanguage(<PriceChart symbol="SPY" gamma={gamma} candles={[]} />);

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
    const { container } = renderWithLanguage(
      <PriceChart
        symbol="SPY"
        gamma={gamma}
        candles={candlesWithRange}
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

  it("hides ATR bands when the visible candles have zero price range (regression for #59)", () => {
    // With only a single flat O=H=L=C candle (the state right after mount,
    // before a second live poll lands), Lightweight Charts' own vertical
    // auto-scale collapses to a near-zero range — priceToCoordinate then
    // maps the ATR band's real bounds to coordinates thousands of pixels
    // outside the chart. This mock reproduces that exact blow-up (a huge,
    // constant offset unrelated to the actual price) so the test fails
    // loudly if the zero-range guard in price-chart.tsx regresses.
    chartMocks.priceToCoordinate.mockImplementation((price: number) => 20_000 - price * 40);
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
    const { container } = renderWithLanguage(
      <PriceChart
        symbol="SPY"
        gamma={gamma}
        candles={[{ time: 1_785_763_800, open: 500, high: 500, low: 500, close: 500 }]}
        atrRange={readyAtrRange}
      />,
    );

    expect(container.querySelector(".atr-band-outer")).toBeNull();
    expect(container.querySelector(".atr-band-inner")).toBeNull();
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
    const { container } = renderWithLanguage(
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
    const { container } = renderWithLanguage(
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
    const { container } = renderWithLanguage(
      <PriceChart
        symbol="SPY"
        gamma={gamma}
        candles={candlesWithRange}
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
    renderWithLanguage(<PriceChart symbol="SPY" gamma={gamma} candles={[]} />);

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
    const { container } = renderWithLanguage(
      <PriceChart symbol="SPY" gamma={gamma} candles={candlesWithRange} atrRange={readyAtrRange} />,
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

  it("expands vertical autoscale to include Gamma levels and ATR bands beyond a single flat candle (regression for the #60 follow-up)", () => {
    // Exact reported scenario: one flat candle (no second poll yet) with
    // every reference level far above it — Lightweight Charts' default
    // autoscale only looks at candle data, so without this the levels
    // would be computed but drawn off-canvas, never visible.
    const flatCandle: MinuteCandle[] = [
      { time: 1_785_763_800, open: 552.25, high: 552.25, low: 552.25, close: 552.25 },
    ];
    const farGamma: GammaResponse = {
      ...gamma,
      put_wall: 570,
      gamma_flip: 590,
      absolute_gamma_strike: 600,
      call_wall: 610,
    };
    const farAtrRange: AtrRange = {
      atr: 20,
      atr_provisional: false,
      daily_bars_count: 15,
      today_open: 552.25,
      bands_provisional: false,
      outer_upper_band: 630,
      outer_lower_band: 460,
      inner_upper_band: 610,
      inner_lower_band: 480,
    };

    renderWithLanguage(<PriceChart symbol="SPY" gamma={farGamma} candles={flatCandle} atrRange={farAtrRange} />);

    const [, options] = chartMocks.addSeries.mock.calls.find(
      ([definition]) => definition === "CandlestickSeries",
    )!;
    expect(options.autoscaleInfoProvider).toBeTypeOf("function");

    const candleOnlyRange = () => ({
      priceRange: { minValue: 551, maxValue: 553 },
      margins: { above: 0.1, below: 0.1 },
    });
    const merged = options.autoscaleInfoProvider(candleOnlyRange);

    // outer_lower_band (460) is the lowest of every candidate level, and
    // outer_upper_band (630) the highest — both must survive the merge,
    // not just the Gamma levels or just the candle range alone.
    expect(merged).toEqual({
      priceRange: { minValue: 460, maxValue: 630 },
      margins: { above: 0.1, below: 0.1 },
    });
  });

  it("excludes ATR bounds from autoscale when the ATR overlay is toggled off, but keeps Gamma levels", () => {
    const flatCandle: MinuteCandle[] = [
      { time: 1_785_763_800, open: 552.25, high: 552.25, low: 552.25, close: 552.25 },
    ];
    const farAtrRange: AtrRange = {
      atr: 20,
      atr_provisional: false,
      daily_bars_count: 15,
      today_open: 552.25,
      bands_provisional: false,
      outer_upper_band: 630,
      outer_lower_band: 460,
      inner_upper_band: 610,
      inner_lower_band: 480,
    };

    const { rerender } = renderWithLanguage(
      <PriceChart symbol="SPY" gamma={gamma} candles={flatCandle} atrRange={farAtrRange} />,
    );

    // Toggle "Rango ATR" off before reading the provider so it reflects
    // the current showAtr state.
    const checkbox = screen.getByRole("checkbox", { name: "Rango ATR" }) as HTMLInputElement;
    checkbox.click();
    rerender(<PriceChart symbol="SPY" gamma={gamma} candles={flatCandle} atrRange={farAtrRange} />);

    const [, options] = chartMocks.addSeries.mock.calls.find(
      ([definition]) => definition === "CandlestickSeries",
    )!;
    const merged = options.autoscaleInfoProvider(() => null);

    // Only Gamma levels (540-560 from the shared `gamma` fixture) remain —
    // 460/630 from the ATR range must not leak in once its overlay is off.
    expect(merged.priceRange.minValue).toBe(540);
    expect(merged.priceRange.maxValue).toBe(560);
  });

  it("nudges the price scale to recompute autoscale whenever Gamma or ATR reference levels change", () => {
    const { rerender } = renderWithLanguage(
      <PriceChart symbol="SPY" gamma={gamma} candles={candlesWithRange} />,
    );
    chartMocks.setData.mockClear();

    rerender(
      <PriceChart symbol="SPY" gamma={{ ...gamma, call_wall: 999 }} candles={candlesWithRange} />,
    );

    // A prop change alone doesn't make the library recompute on its own.
    // `priceScale.setAutoScale(false)` then `(true)` was tried first and
    // confirmed live (against the real library, not this mock) to NOT
    // force a recompute — the visible range stayed stale through it.
    // Re-feeding the series its own unchanged candle data does force it,
    // which is what's asserted here.
    expect(chartMocks.setData).toHaveBeenCalledWith(
      candlesWithRange.map((candle) => ({ ...candle, time: candle.time })),
    );
  });
});
