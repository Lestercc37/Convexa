import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import type { OptionChainResponse, OptionContract } from "@/lib/types";
import { VolatilitySmile } from "./volatility-smile";

const apiMocks = vi.hoisted(() => ({ getOptionChain: vi.fn() }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, getOptionChain: apiMocks.getOptionChain };
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
  apiMocks.getOptionChain.mockImplementation(
    (_symbol: string, expiration?: string) =>
      Promise.resolve(
        chain(
          expiration
            ? contracts.filter((item) => item.expiration === expiration)
            : contracts,
        ),
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
});

function withinOptions(selector: HTMLElement): string[] {
  return Array.from(selector.querySelectorAll("option"), (option) => option.value);
}
