import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TickMarkType } from "lightweight-charts";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { MinuteCandle } from "@/lib/candles";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import { regularSessionRange } from "@/lib/market-session";
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
  applyOptions: vi.fn(),
  priceToCoordinate: vi.fn(),
  coordinateToPrice: vi.fn(),
  coordinateToTime: vi.fn(),
  subscribeVisibleLogicalRangeChange: vi.fn(),
  unsubscribeVisibleLogicalRangeChange: vi.fn(),
  subscribeSizeChange: vi.fn(),
  unsubscribeSizeChange: vi.fn(),
  fitContent: vi.fn(),
  setVisibleRange: vi.fn(),
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
  // Real values from the library itself (lightweight-charts.development.mjs)
  // -- price-chart.tsx switches on these in tickMarkFormatter.
  TickMarkType: { Year: 0, Month: 1, DayOfMonth: 2, Time: 3, TimeWithSeconds: 4 },
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
          coordinateToPrice: chartMocks.coordinateToPrice,
        }
      : { setData: chartMocks.lineSetData },
  );
  chartMocks.createChart.mockReturnValue({
    addSeries: chartMocks.addSeries,
    removeSeries: chartMocks.removeSeries,
    applyOptions: chartMocks.applyOptions,
    timeScale: () => ({
      fitContent: chartMocks.fitContent,
      setVisibleRange: chartMocks.setVisibleRange,
      coordinateToTime: chartMocks.coordinateToTime,
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
  chartMocks.coordinateToPrice.mockImplementation(() => null);
  chartMocks.coordinateToTime.mockImplementation(() => null);
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
    vi.setSystemTime(new Date("2026-08-06T15:00:00Z")); // 11:00 ET, same day as the fixture candle
    const sessionOpenAnchor = regularSessionRange(Date.now()).openSeconds - 1;

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
    // A leading whitespace point (time only, no OHLC) anchors the session
    // open ahead of the real candle -- see withSessionOpenAnchor.
    expect(chartMocks.setData).toHaveBeenCalledWith([
      { time: sessionOpenAnchor },
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

    vi.useRealTimers();
  });

  it("anchors the x-axis at the session open even with only a handful of candles (regression)", () => {
    // Plain fitContent() zooms into just the sliver of real data -- right
    // after the open there's almost none, so the chart would read as if
    // the session started at whatever the first real candle happens to
    // be, not 09:30 ET. A leading whitespace point one second before the
    // open (see withSessionOpenAnchor) fixes fitContent()'s left edge at
    // the real session start regardless of how few candles exist.
    //
    // A *trailing* anchor at 16:00 was tried first and confirmed live
    // (against the real library, not this mock) to NOT hold: lightweight-
    // charts trims trailing whitespace back to the last real bar via
    // every range API tried (fitContent, setVisibleRange,
    // setVisibleLogicalRange) -- so this can only pin the start of the
    // session, not pre-render empty space out to the close while the
    // market is still open. That's a real library constraint, not an
    // oversight here.
    vi.setSystemTime(new Date("2026-09-03T14:00:00Z")); // 10:00 ET
    const sessionOpenAnchor = regularSessionRange(Date.now()).openSeconds - 1;

    renderWithLanguage(
      <PriceChart
        symbol="SPY"
        gamma={gamma}
        candles={[{ time: 1_788_527_400, open: 548, high: 549, low: 548, close: 548.5 }]}
      />,
    );

    expect(chartMocks.setData).toHaveBeenCalledWith([
      { time: sessionOpenAnchor },
      { time: 1_788_527_400, open: 548, high: 549, low: 548, close: 548.5 },
    ]);
    expect(chartMocks.fitContent).toHaveBeenCalled();

    vi.useRealTimers();
  });

  it("formats axis tick marks in Eastern time, not the UTC digits lightweight-charts uses by default (regression)", () => {
    // lightweight-charts formats every tick mark from the *UTC* digits of
    // the given epoch second (confirmed by reading its own source) --
    // left uncorrected, a real 15:59 ET candle (a minute before the
    // regular session's close) prints as "19:59" during EDT, or "20:59"
    // during EST, making perfectly in-session data look like extended
    // hours. Confirmed live, 2026-09.
    renderWithLanguage(<PriceChart symbol="SPY" gamma={gamma} candles={[]} />);

    const chartOptions = chartMocks.createChart.mock.calls.at(-1)![1];
    const formatter = chartOptions.timeScale.tickMarkFormatter;

    // 19:59 UTC on a day inside EDT (UTC-4) -- must read as 15:59 ET, not
    // the raw UTC digits "19:59".
    const edtEpochSeconds = Date.UTC(2026, 8, 3, 19, 59, 0) / 1000;
    expect(formatter(edtEpochSeconds, TickMarkType.Time, "en-US")).toBe("15:59");

    // The same real ET wall-clock time (15:59) during EST (UTC-5) is a
    // *different* UTC epoch (20:59 UTC) -- not a fixed 4-hour offset.
    const estEpochSeconds = Date.UTC(2026, 0, 15, 20, 59, 0) / 1000;
    expect(formatter(estEpochSeconds, TickMarkType.Time, "en-US")).toBe("15:59");
  });

  it("labels the chart title with the selected timeframe", () => {
    renderWithLanguage(
      <PriceChart
        symbol="SPY"
        gamma={gamma}
        candles={[
          { time: 1_786_026_600, open: 548, high: 552, low: 548, close: 550 },
        ]}
        timeframe="15m"
      />,
    );

    expect(screen.getByText("SPY · Velas de 15 minutos")).toBeInTheDocument();
    expect(screen.queryByText("SPY · Velas de 1 minuto")).not.toBeInTheDocument();
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

  it("skips the Gamma Flip price line instead of crashing when gamma_flip is null (regression)", () => {
    // gamma_flip=null is a legitimate "no sign crossing found" value, not
    // an error, and occurs with real frequency on single-stock symbols.
    // series.createPriceLine() asserts its price is a number and throws
    // otherwise (confirmed crash: META/NVDA/SPX/SPY/TSLA) -- the type
    // still says `number` (see lib/types.ts) pending the coordinated
    // design decision, so this is the real runtime shape despite that.
    renderWithLanguage(
      <PriceChart
        symbol="SPY"
        gamma={{ ...gamma, gamma_flip: null as unknown as number }}
        candles={[]}
      />,
    );

    // Put Wall, Abs. Gamma, Call Wall -- Gamma Flip is skipped, not
    // drawn with a null price.
    expect(chartMocks.createPriceLine).toHaveBeenCalledTimes(3);
    expect(chartMocks.createPriceLine).not.toHaveBeenCalledWith(
      expect.objectContaining({ price: null }),
    );
    expect(chartMocks.createPriceLine).not.toHaveBeenCalledWith(
      expect.objectContaining({ title: "Gamma Flip" }),
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
    // 3 historical levels + 1 for the trendline series' initial (empty)
    // setData call, made once at mount regardless of whether anything has
    // been drawn.
    await waitFor(() => expect(chartMocks.lineSetData).toHaveBeenCalledTimes(4));

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

  it("drops a null-valued historical point instead of crashing (regression)", async () => {
    // Same legitimate-null situation as the static-mode price line above,
    // but for a history item -- historyItem.gamma_flip can also be null.
    // series.setData() asserts every point's value is a number and
    // throws otherwise, so the null point is dropped rather than passed
    // through, keeping any other real point for that same level.
    chartMocks.getGammaHistory.mockResolvedValue({
      schema_version: 1,
      symbol: "SPY",
      items: [
        {
          schema_version: 1,
          symbol: "SPY",
          as_of: "2026-08-03T14:00:00Z",
          gamma_flip: 545,
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
        {
          schema_version: 1,
          symbol: "SPY",
          as_of: "2026-08-03T14:30:00Z",
          gamma_flip: null as unknown as number,
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
    const user = userEvent.setup();
    renderWithLanguage(<PriceChart symbol="SPY" gamma={gamma} candles={[]} />);

    await user.click(screen.getByRole("button", { name: "Histórico" }));
    await waitFor(() => expect(chartMocks.lineSetData).toHaveBeenCalledTimes(4));

    expect(chartMocks.lineSetData).not.toHaveBeenCalledWith(
      expect.arrayContaining([expect.objectContaining({ value: null })]),
    );
    // Gamma Flip's own series is the 2nd of the 3 historical additions
    // (HISTORICAL_LEVELS order: Call Wall, Gamma Flip, Put Wall) -- only
    // the point with a real value survives.
    expect(chartMocks.lineSetData.mock.calls[2][0]).toEqual([
      { time: 1_785_765_600, value: 545 },
    ]);
  });

  it("collapses two history items that round to the same second, keeping the latest (regression)", async () => {
    // Confirmed live, 2026-09 investigation: two genuinely separate
    // GammaAggregate rows for SPY, saved ~373ms apart
    // (2026-09-02T20:39:38.056402 and .429345 ET -- both real database
    // inserts, not a query/merge artifact), floor to the exact same
    // whole-second UTCTimestamp once mapped for the chart. setData()
    // requires strictly ascending, unique-per-point time and threw:
    // "data must be asc ordered by time, index=645, time=1788395978,
    // prev time=1788395978". dedupeAscendingByTime (already used for
    // VWAP below) fixes this the same way it already does there.
    chartMocks.getGammaHistory.mockResolvedValue({
      schema_version: 1,
      symbol: "SPY",
      items: [
        {
          schema_version: 1,
          symbol: "SPY",
          as_of: "2026-09-03T00:39:38.056Z",
          gamma_flip: 765.63,
          call_wall: 766,
          put_wall: 765,
          absolute_gamma_strike: 765,
          max_pain: 766,
          net_gamma: 1,
          vega_exposure: 2,
          theta_exposure: 3,
          charm_exposure: 4,
          vanna_exposure: 5,
          dealer_position: "long_gamma",
        },
        {
          // Same real second once floored to UTCTimestamp, distinct
          // sub-second timestamp and (deliberately, to prove the *later*
          // one wins) a different gamma_flip than the row above.
          schema_version: 1,
          symbol: "SPY",
          as_of: "2026-09-03T00:39:38.429Z",
          gamma_flip: 765.99,
          call_wall: 766,
          put_wall: 765,
          absolute_gamma_strike: 765,
          max_pain: 766,
          net_gamma: 1,
          vega_exposure: 2,
          theta_exposure: 3,
          charm_exposure: 4,
          vanna_exposure: 5,
          dealer_position: "long_gamma",
        },
      ],
    });
    const user = userEvent.setup();
    renderWithLanguage(<PriceChart symbol="SPY" gamma={gamma} candles={[]} />);

    await user.click(screen.getByRole("button", { name: "Histórico" }));
    await waitFor(() => expect(chartMocks.lineSetData).toHaveBeenCalledTimes(4));

    // Gamma Flip's own series is the 2nd of the 3 historical additions
    // (HISTORICAL_LEVELS order: Call Wall, Gamma Flip, Put Wall) -- one
    // point, not two, and it's the later (higher gamma_flip) value.
    expect(chartMocks.lineSetData.mock.calls[2][0]).toEqual([
      { time: 1_788_395_978, value: 765.99 },
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
    vi.setSystemTime(new Date("2026-08-03T15:00:00Z")); // 11:00 ET -- matches candlesWithRange's own day
    const sessionOpenAnchor = regularSessionRange(Date.now()).openSeconds - 1;

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
    // which is what's asserted here -- still carrying the session-open
    // anchor, since this setData() call replaces the whole series.
    expect(chartMocks.setData).toHaveBeenCalledWith([
      { time: sessionOpenAnchor },
      ...candlesWithRange.map((candle) => ({ ...candle, time: candle.time })),
    ]);

    vi.useRealTimers();
  });
});

describe("PriceChart trendline drawing", () => {
  it("toggles draw mode, disabling and re-enabling chart pan/zoom", async () => {
    const user = userEvent.setup();
    renderWithLanguage(<PriceChart symbol="SPY" gamma={gamma} candles={candlesWithRange} />);

    const drawButton = screen.getByRole("button", { name: "Línea de tendencia" });
    expect(drawButton).toHaveAttribute("aria-pressed", "false");

    await user.click(drawButton);
    expect(drawButton).toHaveAttribute("aria-pressed", "true");
    expect(chartMocks.applyOptions).toHaveBeenLastCalledWith({
      handleScroll: false,
      handleScale: false,
    });

    await user.click(drawButton);
    expect(drawButton).toHaveAttribute("aria-pressed", "false");
    expect(chartMocks.applyOptions).toHaveBeenLastCalledWith({
      handleScroll: true,
      handleScale: true,
    });
  });

  it("draws a trendline via click-drag, without disturbing the VWAP overlay already on the chart", async () => {
    const user = userEvent.setup();
    chartMocks.coordinateToTime.mockImplementation((x: number) => 1_786_026_600 + x);
    chartMocks.coordinateToPrice.mockImplementation((y: number) => 550 - y);

    renderWithLanguage(
      <PriceChart
        symbol="SPY"
        gamma={gamma}
        candles={candlesWithRange}
        vwapPoints={[{ timestamp: "2026-08-03T13:30:00Z", value: 549 }]}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Línea de tendencia" }));
    const chartContainer = screen.getByLabelText("Chart de velas para SPY");
    const clearButton = screen.getByRole("button", { name: "Borrar línea" });
    expect(clearButton).toBeDisabled();

    act(() => {
      chartContainer.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true, clientX: 0, clientY: 50 }),
      );
      chartContainer.dispatchEvent(
        new MouseEvent("mousemove", { bubbles: true, clientX: 10, clientY: 40 }),
      );
      window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    });

    // Drag start: time 1_786_026_600, price 500. Drag end: time
    // 1_786_026_610, price 510 — already ascending by time.
    expect(chartMocks.lineSetData).toHaveBeenCalledWith([
      { time: 1_786_026_600, value: 500 },
      { time: 1_786_026_610, value: 510 },
    ]);
    // The VWAP overlay (a separate LineSeries sharing this same mocked
    // setData) is unaffected by the trendline being drawn alongside it.
    expect(chartMocks.lineSetData).toHaveBeenCalledWith([
      { time: 1_785_763_800, value: 549 },
    ]);
    expect(clearButton).not.toBeDisabled();

    await user.click(clearButton);
    expect(chartMocks.lineSetData).toHaveBeenLastCalledWith([]);
    expect(clearButton).toBeDisabled();
  });

  it("ignores a drag that never leaves the starting bar (start and end resolve to the same time)", async () => {
    const user = userEvent.setup();
    chartMocks.coordinateToTime.mockImplementation(() => 1_786_026_600);
    chartMocks.coordinateToPrice.mockImplementation((y: number) => 550 - y);

    renderWithLanguage(<PriceChart symbol="SPY" gamma={gamma} candles={candlesWithRange} />);
    await user.click(screen.getByRole("button", { name: "Línea de tendencia" }));
    const chartContainer = screen.getByLabelText("Chart de velas para SPY");

    act(() => {
      chartContainer.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true, clientX: 0, clientY: 50 }),
      );
      chartContainer.dispatchEvent(
        new MouseEvent("mousemove", { bubbles: true, clientX: 3, clientY: 40 }),
      );
      window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    });

    expect(screen.getByRole("button", { name: "Borrar línea" })).toBeDisabled();
    expect(chartMocks.lineSetData).not.toHaveBeenCalledWith([
      expect.anything(),
      expect.anything(),
    ]);
  });

  it("draws nothing for a plain click with no drag", async () => {
    const user = userEvent.setup();
    chartMocks.coordinateToTime.mockImplementation((x: number) => 1_786_026_600 + x);
    chartMocks.coordinateToPrice.mockImplementation((y: number) => 550 - y);

    renderWithLanguage(<PriceChart symbol="SPY" gamma={gamma} candles={candlesWithRange} />);
    await user.click(screen.getByRole("button", { name: "Línea de tendencia" }));
    const chartContainer = screen.getByLabelText("Chart de velas para SPY");

    act(() => {
      chartContainer.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true, clientX: 0, clientY: 50 }),
      );
      window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    });

    expect(screen.getByRole("button", { name: "Borrar línea" })).toBeDisabled();
  });

  it("does not draw while draw mode is off, even on click-drag over the chart", async () => {
    chartMocks.coordinateToTime.mockImplementation((x: number) => 1_786_026_600 + x);
    chartMocks.coordinateToPrice.mockImplementation((y: number) => 550 - y);

    renderWithLanguage(<PriceChart symbol="SPY" gamma={gamma} candles={candlesWithRange} />);
    const chartContainer = screen.getByLabelText("Chart de velas para SPY");

    act(() => {
      chartContainer.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true, clientX: 0, clientY: 50 }),
      );
      chartContainer.dispatchEvent(
        new MouseEvent("mousemove", { bubbles: true, clientX: 10, clientY: 40 }),
      );
      window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    });

    expect(screen.getByRole("button", { name: "Borrar línea" })).toBeDisabled();
    expect(chartMocks.applyOptions).not.toHaveBeenCalledWith(
      expect.objectContaining({ handleScroll: false }),
    );
  });
});
