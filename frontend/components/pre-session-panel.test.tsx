import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { GammaAggregateResponse } from "@/lib/types";
import { PreSessionPanel } from "./pre-session-panel";

const apiMocks = vi.hoisted(() => ({ getGammaProfile: vi.fn() }));

vi.mock("@/lib/api", () => ({ getGammaProfile: apiMocks.getGammaProfile }));

function profile(overrides: Partial<GammaAggregateResponse> = {}): GammaAggregateResponse {
  return {
    schema_version: 1,
    symbol: "SPY",
    as_of: "2026-08-07T20:30:00Z",
    gamma_flip: 548.5,
    max_pain: 550,
    total_market_gamma: 280,
    positive_gamma: 280,
    negative_gamma: 0,
    absolute_gamma_strike: 550,
    peak_gamma_value: 190,
    items: [
      {
        strike: 545,
        total_gamma_exposure: 390,
        call_gamma_exposure: 240,
        put_gamma_exposure: -150,
        net_gamma: 90,
        contract_count: 2,
        absolute_gamma: 90,
      },
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
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("PreSessionPanel", () => {
  it("labels the snapshot as frozen from the previous close and draws per-strike bars", async () => {
    apiMocks.getGammaProfile.mockResolvedValue(profile());

    render(<PreSessionPanel symbol="SPY" />);

    expect(
      await screen.findByText(/Congelado desde el cierre de viernes, 7 de agosto de 2026/),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Strike 545")).toBeInTheDocument();
    expect(screen.getByLabelText("Strike 550")).toBeInTheDocument();
    expect(screen.getByLabelText("Gamma Flip 548.5")).toBeInTheDocument();
    expect(screen.getByLabelText("Max Pain 550")).toBeInTheDocument();
    expect(apiMocks.getGammaProfile).toHaveBeenCalledWith("SPY", expect.any(AbortSignal));
  });

  it("does not poll — fetches the frozen snapshot exactly once per symbol", async () => {
    apiMocks.getGammaProfile.mockResolvedValue(profile());

    render(<PreSessionPanel symbol="SPY" />);

    await screen.findByLabelText("Strike 545");
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(apiMocks.getGammaProfile).toHaveBeenCalledTimes(1);
  });

  it("shows an error when no frozen snapshot exists yet for the symbol", async () => {
    apiMocks.getGammaProfile.mockRejectedValue(new Error("No gamma aggregate found for SPY"));

    render(<PreSessionPanel symbol="SPY" />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No gamma aggregate found for SPY",
    );
  });
});
