import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import type { GammaAggregateResponse, WhaleAlert, WhaleAlertsResponse } from "@/lib/types";
import { ChartSecondaryPanel } from "./chart-secondary-panel";

const apiMocks = vi.hoisted(() => ({ getGammaProfile: vi.fn(), getAlerts: vi.fn() }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, getGammaProfile: apiMocks.getGammaProfile, getAlerts: apiMocks.getAlerts };
});

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

function alert(overrides: Partial<WhaleAlert> = {}): WhaleAlert {
  return {
    symbol: "SPY",
    contract: "SPY260220C00550000",
    type: "WHALE",
    amount: 200000,
    timestamp: "2026-08-07T14:30:00Z",
    estimated_buy_volume: 1500,
    estimated_sell_volume: 500,
    ...overrides,
  };
}

function alertsResponse(alerts: WhaleAlert[]): WhaleAlertsResponse {
  return { schema_version: 1, symbol: "SPY", alerts };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.getGammaProfile.mockResolvedValue(profile());
  apiMocks.getAlerts.mockResolvedValue(alertsResponse([]));
});

describe("ChartSecondaryPanel", () => {
  it("renders the GEX-by-strike view by default, with a bar per strike", async () => {
    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" />);

    await waitFor(() => expect(apiMocks.getGammaProfile).toHaveBeenCalledWith("SPY", expect.any(AbortSignal)));

    expect(await screen.findByLabelText("GEX por strike para SPY")).toBeInTheDocument();
    expect(screen.getByLabelText("Strike 545")).toBeInTheDocument();
    expect(screen.getByLabelText("Strike 550")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "GEX por Strike" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("toggles from the GEX view to the Whale Alerts flow view and back", async () => {
    const user = userEvent.setup();
    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" />);
    await screen.findByLabelText("GEX por strike para SPY");

    await user.click(screen.getByRole("button", { name: "Flujo Whale Alerts" }));

    expect(screen.queryByLabelText("GEX por strike para SPY")).not.toBeInTheDocument();
    expect(await screen.findByText("Sin alertas todavía en esta sesión.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Flujo Whale Alerts" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await user.click(screen.getByRole("button", { name: "GEX por Strike" }));
    expect(await screen.findByLabelText("GEX por strike para SPY")).toBeInTheDocument();
  });

  it("shows a translated error when the GEX profile fetch fails", async () => {
    apiMocks.getGammaProfile.mockRejectedValue(new ApiError(404));

    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("No se encontró el recurso solicitado.");
  });

  it("accumulates Whale Alerts across polls, deduping repeats, into a growing net-flow line", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const firstAlert = alert({
      contract: "SPY260220C00550000",
      timestamp: "2026-08-07T14:30:00Z",
      estimated_buy_volume: 1500,
      estimated_sell_volume: 500,
    });
    const secondAlert = alert({
      contract: "SPY260220P00545000",
      timestamp: "2026-08-07T14:35:00Z",
      estimated_buy_volume: 200,
      estimated_sell_volume: 1200,
    });
    apiMocks.getAlerts
      .mockResolvedValueOnce(alertsResponse([firstAlert]))
      .mockResolvedValue(alertsResponse([secondAlert, firstAlert]));

    // Computed the same way the component derives it (toLocaleTimeString
    // on the raw timestamp) so this assertion doesn't hardcode a specific
    // timezone offset — it only needs to match whatever this machine's
    // local timezone renders, same as the component does.
    const sinceCaption = `Datos desde las ${new Date(firstAlert.timestamp).toLocaleTimeString()} — memoria del backend, sin persistencia`;

    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" />);
    await user.click(screen.getByRole("button", { name: "Flujo Whale Alerts" }));
    await waitFor(() => expect(apiMocks.getAlerts).toHaveBeenCalledTimes(1));
    // First poll only: net flow so far is +1000 (1500 buy - 500 sell) from
    // the 14:30 alert alone — the caption already reflects it.
    expect(await screen.findByText(sinceCaption)).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(30_000);
    await waitFor(() => expect(apiMocks.getAlerts).toHaveBeenCalledTimes(2));

    // Second poll repeats the first alert (same alertKey: symbol+contract+
    // timestamp) and adds one genuinely new one — the accumulated set
    // must end up with exactly 2 points, not 3, and the caption's "since"
    // time must not have moved forward just because a poll ran.
    expect(screen.getByText(sinceCaption)).toBeInTheDocument();
    const line = document.querySelector(".secondary-flow-line");
    expect(line).not.toBeNull();
    const points = line?.getAttribute("points")?.trim().split(/\s+/) ?? [];
    expect(points).toHaveLength(2);

    vi.useRealTimers();
  });

  it("shows the loading state before the first alerts poll resolves", async () => {
    let resolveAlerts: (value: WhaleAlertsResponse) => void = () => {};
    apiMocks.getAlerts.mockImplementation(
      () => new Promise((resolve) => { resolveAlerts = resolve; }),
    );
    const user = userEvent.setup();

    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" />);
    await user.click(screen.getByRole("button", { name: "Flujo Whale Alerts" }));

    expect(screen.getByText("Cargando flujo de Whale Alerts…")).toBeInTheDocument();

    resolveAlerts(alertsResponse([]));
    expect(await screen.findByText("Sin alertas todavía en esta sesión.")).toBeInTheDocument();
  });
});
