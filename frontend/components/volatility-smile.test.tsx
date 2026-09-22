import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import type { OptionChainResponse, OptionContract } from "@/lib/types";
import { VolatilitySmile } from "./volatility-smile";

const apiMocks = vi.hoisted(() => ({
  getOptionChain: vi.fn(),
  getOptionChainExpirations: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getOptionChain: apiMocks.getOptionChain,
    getOptionChainExpirations: apiMocks.getOptionChainExpirations,
  };
});

function contract(
  occSymbol: string,
  strike: number,
  expiration: string,
  type: "call" | "put",
  iv: number,
): OptionContract {
  return {
    occ_symbol: occSymbol,
    strike,
    expiration,
    type,
    bid: 1,
    ask: 1.1,
    iv,
    delta: 0.5,
    gamma: 0.02,
    theta: -0.01,
    vega: 0.1,
    charm: -0.001,
    vanna: 0.01,
    open_interest: 1000,
    volume: 500,
  };
}

const contracts = [
  contract("SPY260807C00545000", 545, "2026-08-07", "call", 0.22),
  contract("SPY260807P00550000", 550, "2026-08-07", "put", 0.19),
  contract("SPY260807C00555000", 555, "2026-08-07", "call", 0.21),
  contract("SPY260814C00550000", 550, "2026-08-14", "call", 0.2),
  contract("SPY260814P00555000", 555, "2026-08-14", "put", 0.23),
];

function chain(filteredContracts: OptionContract[]): OptionChainResponse {
  return {
    schema_version: 1,
    symbol: "SPY",
    as_of: "2026-08-03T14:30:00Z",
    spot_price: 551,
    contracts: filteredContracts,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.getOptionChainExpirations.mockResolvedValue({
    schema_version: 1,
    symbol: "SPY",
    expirations: [...new Set(contracts.map((item) => item.expiration))],
  });
  apiMocks.getOptionChain.mockImplementation((_symbol: string, expiration?: string) =>
    Promise.resolve(
      chain(expiration ? contracts.filter((item) => item.expiration === expiration) : contracts),
    ),
  );
});

describe("VolatilitySmile", () => {
  it("renders raw call/put IV points, derives expirations and marks ATM", async () => {
    const user = userEvent.setup();
    renderWithLanguage(<VolatilitySmile symbol="SPY" marketPrice={551} />);

    const selector = await screen.findByLabelText("Vencimiento");
    await waitFor(() => expect(selector).toHaveValue("2026-08-07"));
    expect(withinOptions(selector)).toEqual(["2026-08-07", "2026-08-14"]);
    expect(await screen.findByLabelText("Strike ATM 550")).toBeInTheDocument();
    expect(screen.getByLabelText("call strike 545, IV 22.00%")).toBeInTheDocument();
    expect(screen.getByLabelText("put strike 550, IV 19.00%")).toBeInTheDocument();
    expect(screen.getByText(/no es un nivel de gravitación/)).toBeInTheDocument();

    await user.selectOptions(selector, "2026-08-14");
    await waitFor(() =>
      expect(apiMocks.getOptionChain).toHaveBeenCalledWith(
        "SPY",
        "2026-08-14",
        expect.any(AbortSignal),
      ),
    );
    expect(await screen.findByLabelText("call strike 550, IV 20.00%")).toBeInTheDocument();
    expect(screen.queryByLabelText("call strike 545, IV 22.00%")).not.toBeInTheDocument();
  });

  it("re-polls the option chain every 30s instead of freezing at the value fetched on mount (regression)", async () => {
    // Confirmed live, 2026-09-17: IV per contract moves within a session
    // the same as everything else derived from the option chain, but this
    // panel only ever fetched once per symbol/expiration change -- same
    // 30s cadence as dashboard.tsx's own live refresh (polling.ts's
    // POLLING_INTERVAL_MS), not a new one invented for this panel.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderWithLanguage(<VolatilitySmile symbol="SPY" marketPrice={551} />);

    await vi.waitFor(() => expect(apiMocks.getOptionChain).toHaveBeenCalledTimes(1));

    await vi.advanceTimersByTimeAsync(30_000);
    await vi.waitFor(() => expect(apiMocks.getOptionChain).toHaveBeenCalledTimes(2));

    vi.useRealTimers();
  });

  it("populates the expiration dropdown from the lightweight expirations endpoint, not the full chain (regression)", async () => {
    // getOptionChain (the full, per-contract chain) must never be called
    // without an expiration -- confirmed live, 2026-09-22: doing so just
    // to read off `.expiration` grew to ~8,000 contracts / 2.3MB for SPX
    // after the Gamma Flip wide-search fix (PR #159), heavy enough to
    // help starve /chain/{symbol}'s shared threadpool into real 500s.
    renderWithLanguage(<VolatilitySmile symbol="SPY" marketPrice={551} />);

    await screen.findByLabelText("Vencimiento");
    expect(apiMocks.getOptionChainExpirations).toHaveBeenCalledWith(
      "SPY",
      expect.any(AbortSignal),
    );
    expect(apiMocks.getOptionChain).not.toHaveBeenCalledWith(
      "SPY",
      undefined,
      expect.any(AbortSignal),
    );
  });

  it("gives each IV point a hover tooltip with the same text as its aria-label (regression)", async () => {
    renderWithLanguage(<VolatilitySmile symbol="SPY" marketPrice={551} />);

    const point = await screen.findByLabelText("call strike 545, IV 22.00%");
    expect(point.querySelector("title")).toHaveTextContent("call strike 545, IV 22.00%");
  });
});

function withinOptions(selector: HTMLElement): string[] {
  return Array.from(selector.querySelectorAll("option"), (option) => option.value);
}
