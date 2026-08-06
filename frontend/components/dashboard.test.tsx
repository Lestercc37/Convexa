import { render, screen, waitFor } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { derivedMetricsFixture } from "@/test/fixtures";
import { Dashboard } from "./dashboard";

const apiMocks = vi.hoisted(() => ({
  getGamma: vi.fn(),
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
  ColorType: { Solid: "solid" },
  LineStyle: { Dashed: 2 },
  createChart: chartMocks.createChart,
}));

beforeAll(() => {
  chartMocks.createChart.mockReturnValue({
    addSeries: () => ({
      setData: vi.fn(),
      update: vi.fn(),
      createPriceLine: vi.fn(() => ({})),
      removePriceLine: vi.fn(),
    }),
    applyOptions: vi.fn(),
    timeScale: () => ({ fitContent: vi.fn() }),
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

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.getUnderlyings.mockResolvedValue({
    schema_version: 1,
    underlyings: [{ symbol: "SPY", kind: "equity", is_priority: true }],
  });
  apiMocks.getGamma.mockResolvedValue({
    schema_version: 1,
    symbol: "SPY",
    as_of: "2026-08-03T14:30:00Z",
    gamma_flip: 548.5,
    call_wall: 560,
    put_wall: 540,
    absolute_gamma_strike: 550,
    dealer_position: "long_gamma",
    derived_metrics: derivedMetricsFixture,
  });
  apiMocks.getMarket.mockResolvedValue({
    schema_version: 1,
    symbol: "SPY",
    as_of: "2026-08-03T14:30:05Z",
    price: 549.1,
    volume: 1_000_000,
    dealer_mode: "long_gamma",
    dealer_mode_source: "agree",
    dealer_mode_confirmed: true,
  });
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
});

describe("Dashboard", () => {
  it("renders all current panels without duplicate-key warnings", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    render(<Dashboard />);

    await screen.findByLabelText("Chart de velas para SPY");
    await waitFor(() => expect(apiMocks.getOptionChain).toHaveBeenCalled());

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
});
