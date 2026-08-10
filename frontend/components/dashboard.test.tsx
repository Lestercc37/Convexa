import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { derivedMetricsFixture } from "@/test/fixtures";
import { Dashboard } from "./dashboard";

const apiMocks = vi.hoisted(() => ({
  getAlerts: vi.fn(),
  getGamma: vi.fn(),
  getGammaProfile: vi.fn(),
  getMarket: vi.fn(),
  getOptionChain: vi.fn(),
  getScreenerPreset: vi.fn(),
  getUnderlyings: vi.fn(),
}));

const chartMocks = vi.hoisted(() => ({
  createChart: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);
vi.mock("lightweight-charts", () => ({
  CandlestickSeries: "CandlestickSeries",
  LineSeries: "LineSeries",
  ColorType: { Solid: "solid" },
  LineStyle: { Dashed: 2, Solid: 0 },
  createChart: chartMocks.createChart,
}));

beforeAll(() => {
  chartMocks.createChart.mockReturnValue({
    addSeries: () => ({
      setData: vi.fn(),
      update: vi.fn(),
      createPriceLine: vi.fn(() => ({})),
      removePriceLine: vi.fn(),
      priceToCoordinate: vi.fn(() => 100),
    }),
    removeSeries: vi.fn(),
    applyOptions: vi.fn(),
    timeScale: () => ({
      fitContent: vi.fn(),
      subscribeVisibleLogicalRangeChange: vi.fn(),
      unsubscribeVisibleLogicalRangeChange: vi.fn(),
      subscribeSizeChange: vi.fn(),
      unsubscribeSizeChange: vi.fn(),
    }),
    remove: vi.fn(),
  });
  vi.stubGlobal(
    "ResizeObserver",
    class ResizeObserver {
      observe() {}
      disconnect() {}
    },
  );
});

function gammaFor(symbol: string) {
  return {
    schema_version: 1,
    symbol,
    as_of: "2026-08-03T14:30:00Z",
    gamma_flip: 548.5,
    call_wall: 560,
    put_wall: 540,
    absolute_gamma_strike: 550,
    max_pain: 549,
    net_gamma: 1,
    vega_exposure: 2,
    theta_exposure: 3,
    charm_exposure: 12_500,
    vanna_exposure: -8_300,
    dealer_position: "long_gamma" as const,
    derived_metrics: derivedMetricsFixture,
  };
}

function marketFor(symbol: string) {
  return {
    schema_version: 1,
    symbol,
    as_of: "2026-08-03T14:30:05Z",
    price: 549.1,
    volume: 1_000_000,
    dealer_mode: "long_gamma" as const,
    dealer_mode_source: "agree" as const,
    dealer_mode_confirmed: true,
    anchored_vwap: {
      value: 548,
      provisional: false,
      anchor_time: "2026-08-03T13:30:00Z",
      sample_count: 3,
    },
    atr_range: {
      atr: 5,
      atr_provisional: false,
      daily_bars_count: 15,
      today_open: 548,
      bands_provisional: false,
      outer_upper_band: 553,
      outer_lower_band: 543,
      inner_upper_band: 550.5,
      inner_lower_band: 545.5,
    },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.getUnderlyings.mockResolvedValue({
    schema_version: 1,
    underlyings: [
      { symbol: "SPY", kind: "equity", is_priority: true },
      { symbol: "GOOGL", kind: "equity", is_priority: true },
    ],
  });
  apiMocks.getGamma.mockImplementation((symbol: string) => Promise.resolve(gammaFor(symbol)));
  apiMocks.getMarket.mockImplementation((symbol: string) => Promise.resolve(marketFor(symbol)));
  apiMocks.getOptionChain.mockResolvedValue({
    schema_version: 1,
    symbol: "SPY",
    as_of: "2026-08-03T14:30:00Z",
    spot_price: 549.1,
    contracts: [],
  });
  apiMocks.getScreenerPreset.mockResolvedValue({
    schema_version: 1,
    preset: "unusual-options-activity",
    results: [],
  });
  apiMocks.getAlerts.mockResolvedValue({ schema_version: 1, symbol: "SPY", alerts: [] });
  apiMocks.getGammaProfile.mockImplementation((symbol: string) =>
    Promise.resolve({
      schema_version: 1,
      symbol,
      as_of: "2026-08-07T20:30:00Z",
      gamma_flip: 548.5,
      max_pain: 549,
      total_market_gamma: 280,
      positive_gamma: 280,
      negative_gamma: 0,
      absolute_gamma_strike: 550,
      peak_gamma_value: 190,
      items: [
        {
          strike: 550,
          total_gamma_exposure: 200,
          call_gamma_exposure: 120,
          put_gamma_exposure: -80,
          net_gamma: 40,
          contract_count: 3,
          absolute_gamma: 40,
        },
      ],
    }),
  );
});

describe("Dashboard", () => {
  it("renders all current panels without duplicate-key warnings", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    render(<Dashboard />);

    await screen.findByLabelText("Chart de velas para SPY");
    await waitFor(() => expect(apiMocks.getOptionChain).toHaveBeenCalled());
    await waitFor(() => expect(apiMocks.getAlerts).toHaveBeenCalledWith("SPY", expect.any(AbortSignal)));
    await waitFor(() => expect(apiMocks.getAlerts).toHaveBeenCalledWith("GOOGL", expect.any(AbortSignal)));

    const duplicateKeyWarnings = consoleError.mock.calls.filter((args) =>
      args.some(
        (value) =>
          typeof value === "string" &&
          value.includes("Encountered two children with the same key"),
      ),
    );
    expect(duplicateKeyWarnings).toEqual([]);

    consoleError.mockRestore();
  });

  it("survives repeated symbol switches without crashing (regression for #54/#55)", async () => {
    const user = userEvent.setup();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    render(<Dashboard />);
    await screen.findByLabelText("Chart de velas para SPY");

    const select = screen.getByRole("combobox", { name: "Underlying" });

    // Each switch unmounts and remounts PriceChart via its `key`, which is
    // exactly the scenario that crashed before the cleanup-guard fix
    // (#54) and the duplicate-timestamp dedup fix (#55). SPY here also
    // carries non-provisional anchored_vwap/atr_range data, so the VWAP
    // line and ATR band effects actually mount and tear down each time,
    // not just the static Gamma price lines.
    await user.selectOptions(select, "GOOGL");
    await screen.findByLabelText("Chart de velas para GOOGL");
    await user.selectOptions(select, "SPY");
    await screen.findByLabelText("Chart de velas para SPY");
    await user.selectOptions(select, "GOOGL");
    await screen.findByLabelText("Chart de velas para GOOGL");
    await user.selectOptions(select, "SPY");
    await screen.findByLabelText("Chart de velas para SPY");

    // The dashboard is still fully rendered — not stuck on the error panel.
    expect(screen.getByRole("group", { name: "Timeframe" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(consoleError).not.toHaveBeenCalled();

    consoleError.mockRestore();
  });

  it("switches to the Pre-Sesión view without polling the live chart, and back", async () => {
    const user = userEvent.setup();
    render(<Dashboard />);
    await screen.findByLabelText("Chart de velas para SPY");

    await user.click(screen.getByRole("button", { name: "Pre-Sesión" }));

    expect(await screen.findByText(/Congelado desde el cierre de/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Chart de velas para SPY")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(apiMocks.getGammaProfile).toHaveBeenCalledWith("SPY", expect.any(AbortSignal)),
    );

    await user.click(screen.getByRole("button", { name: "En vivo" }));
    expect(await screen.findByLabelText("Chart de velas para SPY")).toBeInTheDocument();
  });
});
