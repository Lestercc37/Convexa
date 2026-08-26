import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import type { GammaAggregateResponse, GammaResponse, MarketResponse } from "@/lib/types";
import { derivedMetricsFixture } from "@/test/fixtures";
import { PreSessionPanel } from "./pre-session-panel";

const apiMocks = vi.hoisted(() => ({ getGammaProfile: vi.fn() }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, getGammaProfile: apiMocks.getGammaProfile };
});

const gamma: GammaResponse = {
  schema_version: 1,
  symbol: "SPY",
  as_of: "2026-08-03T14:30:00Z",
  gamma_flip: 548.5,
  call_wall: 555,
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

const market: MarketResponse = {
  schema_version: 1,
  symbol: "SPY",
  as_of: "2026-08-03T14:30:05Z",
  price: 549.1,
  volume: 1_000_000,
  dealer_mode: "long_gamma",
  dealer_mode_source: "agree",
  dealer_mode_confirmed: true,
};

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

    renderWithLanguage(<PreSessionPanel symbol="SPY" gamma={gamma} market={market} />);

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

    renderWithLanguage(<PreSessionPanel symbol="SPY" gamma={gamma} market={market} />);

    await screen.findByLabelText("Strike 545");
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(apiMocks.getGammaProfile).toHaveBeenCalledTimes(1);
  });

  it("shows a translated not-found error when no frozen snapshot exists yet for the symbol", async () => {
    apiMocks.getGammaProfile.mockRejectedValue(new ApiError(404));

    renderWithLanguage(<PreSessionPanel symbol="SPY" gamma={gamma} market={market} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No se encontró el recurso solicitado.",
    );
  });

  it("renders the Regime Badge below the frozen chart, regardless of that chart's own load state", async () => {
    apiMocks.getGammaProfile.mockRejectedValue(new ApiError(404));

    renderWithLanguage(<PreSessionPanel symbol="SPY" gamma={gamma} market={market} />);

    await screen.findByRole("alert");
    expect(screen.getByRole("heading", { name: "LONG GAMMA" })).toBeInTheDocument();
  });
});
