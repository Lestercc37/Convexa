import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import type { WhaleAlertsResponse } from "@/lib/types";
import { AlertsPanel } from "./alerts-panel";

const apiMocks = vi.hoisted(() => ({
  getAlerts: vi.fn(),
  getWhaleThresholds: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, ...apiMocks };
});

function alertsResponse(symbol: string, alerts: WhaleAlertsResponse["alerts"]): WhaleAlertsResponse {
  return { schema_version: 1, symbol, alerts };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.getAlerts.mockResolvedValue(alertsResponse("SPY", []));
  apiMocks.getWhaleThresholds.mockResolvedValue({ schema_version: 1, thresholds: [] });
});

describe("AlertsPanel", () => {
  it("queries only the active symbol, not every underlying (confirmed by design, PR product decision)", async () => {
    apiMocks.getAlerts.mockResolvedValue(
      alertsResponse("SPY", [
        {
          symbol: "SPY",
          contract: "SPY260220C00540000",
          type: "UNUSUAL",
          amount: 45000,
          timestamp: "2026-08-03T14:00:00Z",
          estimated_buy_volume: 22500,
          estimated_sell_volume: 22500,
        },
      ]),
    );

    renderWithLanguage(<AlertsPanel symbol="SPY" />);

    await waitFor(() => expect(apiMocks.getAlerts).toHaveBeenCalledTimes(1));
    expect(apiMocks.getAlerts).toHaveBeenCalledWith("SPY", expect.any(AbortSignal));

    const cards = await screen.findAllByRole("article");
    expect(cards).toHaveLength(1);
    expect(cards[0]).toHaveTextContent("SPY");
    expect(cards[0]).toHaveTextContent("Unusual");
    expect(cards[0]).toHaveTextContent("$45,000");
  });

  it("renders two distinct cards when the same contract+timestamp trips two alert types (regression)", async () => {
    // Same collision already fixed in quick-screener.tsx's key: a single
    // reading can independently trip a magnitude threshold (WHALE/
    // UNUSUAL) *and* the separate sustained-flow window, so the same
    // symbol+contract+timestamp legitimately carries two distinct alerts
    // with a different `type`. Before this fix, alertKey() (symbol+
    // contract+timestamp only) collided for both, and React logged
    // "Encountered two children with the same key" for this list.
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    apiMocks.getAlerts.mockResolvedValue(
      alertsResponse("SPY", [
        {
          symbol: "SPY",
          contract: "SPY260904P00772000",
          type: "SUSTAINED_FLOW",
          amount: 596745,
          timestamp: "2026-09-04T13:59:11.879640Z",
          estimated_buy_volume: 300000,
          estimated_sell_volume: 296745,
        },
        {
          symbol: "SPY",
          contract: "SPY260904P00772000",
          type: "WHALE",
          amount: 493889,
          timestamp: "2026-09-04T13:59:11.879640Z",
          estimated_buy_volume: 250000,
          estimated_sell_volume: 243889,
        },
      ]),
    );

    renderWithLanguage(<AlertsPanel symbol="SPY" />);

    const cards = await screen.findAllByRole("article");
    expect(cards).toHaveLength(2);
    expect(screen.getByText("Sustained Flow")).toBeInTheDocument();
    expect(screen.getByText("Whale")).toBeInTheDocument();
    expect(
      consoleError.mock.calls.some((call) => String(call[0]).includes("same key")),
    ).toBe(false);

    consoleError.mockRestore();
  });

  it("re-fetches for the new symbol when the active chart symbol changes, not just on its own 30s interval", async () => {
    apiMocks.getAlerts.mockImplementation((symbol: string) =>
      Promise.resolve(
        symbol === "SPY"
          ? alertsResponse("SPY", [
              {
                symbol: "SPY",
                contract: "SPY260220C00540000",
                type: "UNUSUAL",
                amount: 45000,
                timestamp: "2026-08-03T14:00:00Z",
                estimated_buy_volume: 22500,
                estimated_sell_volume: 22500,
              },
            ])
          : alertsResponse("QQQ", [
              {
                symbol: "QQQ",
                contract: "QQQ260220P00480000",
                type: "WHALE",
                amount: 210000,
                timestamp: "2026-08-03T14:05:00Z",
                estimated_buy_volume: 105000,
                estimated_sell_volume: 105000,
              },
            ]),
      ),
    );

    const { rerender } = renderWithLanguage(<AlertsPanel symbol="SPY" />);
    await waitFor(() => expect(apiMocks.getAlerts).toHaveBeenCalledWith("SPY", expect.any(AbortSignal)));
    let cards = await screen.findAllByRole("article");
    expect(cards).toHaveLength(1);
    expect(cards[0]).toHaveTextContent("SPY");

    rerender(<AlertsPanel symbol="QQQ" />);

    await waitFor(() => expect(apiMocks.getAlerts).toHaveBeenCalledWith("QQQ", expect.any(AbortSignal)));
    cards = await screen.findAllByRole("article");
    expect(cards).toHaveLength(1);
    expect(cards[0]).toHaveTextContent("QQQ");
    // The old SPY card is gone, not accumulated alongside QQQ's.
    expect(screen.queryByText("SPY260220C00540000")).not.toBeInTheDocument();
  });

  it("defaults to the horizontal strip, and switches to a vertical column via orientation", async () => {
    apiMocks.getAlerts.mockResolvedValue(
      alertsResponse("SPY", [
        {
          symbol: "SPY",
          contract: "SPY260220C00540000",
          type: "UNUSUAL",
          amount: 45000,
          timestamp: "2026-08-03T14:00:00Z",
          estimated_buy_volume: 22500,
          estimated_sell_volume: 22500,
        },
      ]),
    );

    const { container: horizontalContainer } = renderWithLanguage(<AlertsPanel symbol="SPY" />);
    await screen.findAllByRole("article");
    expect(horizontalContainer.querySelector(".alerts-row")).toBeInTheDocument();
    expect(horizontalContainer.querySelector(".alerts-column")).not.toBeInTheDocument();

    const { container: verticalContainer } = renderWithLanguage(
      <AlertsPanel symbol="SPY" orientation="vertical" />,
    );
    await screen.findAllByRole("article");
    expect(verticalContainer.querySelector(".alerts-column")).toBeInTheDocument();
    expect(verticalContainer.querySelector(".alerts-row")).not.toBeInTheDocument();
    expect(
      verticalContainer.querySelector(".alerts-panel-vertical"),
    ).toBeInTheDocument();
  });

  it("shows an empty state, without an error, when there are no alerts", async () => {
    apiMocks.getAlerts.mockResolvedValue(alertsResponse("SPY", []));

    renderWithLanguage(<AlertsPanel symbol="SPY" />);

    await waitFor(() => expect(apiMocks.getAlerts).toHaveBeenCalled());
    expect(await screen.findByText("Sin alertas recientes.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  it("shows Calls and Puts mixed in one feed, sorted by time (no more tabs)", async () => {
    // Lester's own request: a single feed, most-recent-first, so Calls
    // vs. Puts dominance is read from how often each appears, not from
    // switching tabs. recent_alerts() (backend) already returns
    // most-recent-first, so the two responses below (call at 14:00,
    // put at 14:05) must render put-first, call-second, both visible
    // at once -- never a per-side tab to click between them.
    apiMocks.getAlerts.mockResolvedValue(
      alertsResponse("SPY", [
        {
          symbol: "SPY",
          contract: "SPY260220P00540000",
          type: "WHALE",
          amount: 210000,
          timestamp: "2026-08-03T14:05:00Z",
          estimated_buy_volume: 60000,
          estimated_sell_volume: 150000,
        },
        {
          symbol: "SPY",
          contract: "SPY260220C00540000",
          type: "UNUSUAL",
          amount: 45000,
          timestamp: "2026-08-03T14:00:00Z",
          estimated_buy_volume: 30000,
          estimated_sell_volume: 15000,
        },
      ]),
    );

    renderWithLanguage(<AlertsPanel symbol="SPY" orientation="vertical" />);

    const cards = await screen.findAllByRole("article");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent("SPY260220P00540000");
    expect(cards[1]).toHaveTextContent("SPY260220C00540000");
    // No Calls/Puts tab buttons anywhere in this panel anymore.
    expect(screen.queryByRole("button", { name: "Calls" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Puts" })).not.toBeInTheDocument();
    // Type (Call/Put) stays visible per card, just not as a tab filter.
    expect(cards[0]).toHaveTextContent("Put");
    expect(cards[1]).toHaveTextContent("Call");
    // BVC estimate rendered on the card, explicitly labeled as an estimate
    // (renderWithLanguage defaults to Spanish).
    expect(cards[0]).toHaveTextContent("29% / 71%");
    expect(cards[0]).toHaveTextContent("Compra/venta estimado (BVC)");
  });

  it("labels each card Compra or Venta based on which side of the BVC split dominates", async () => {
    apiMocks.getAlerts.mockResolvedValue(
      alertsResponse("SPY", [
        {
          // 67% buy / 33% sell -- buy dominates.
          symbol: "SPY",
          contract: "SPY260220C00540000",
          type: "UNUSUAL",
          amount: 45000,
          timestamp: "2026-08-03T14:05:00Z",
          estimated_buy_volume: 30000,
          estimated_sell_volume: 15000,
        },
        {
          // 25% buy / 75% sell -- sell dominates.
          symbol: "SPY",
          contract: "SPY260220P00540000",
          type: "WHALE",
          amount: 210000,
          timestamp: "2026-08-03T14:00:00Z",
          estimated_buy_volume: 15000,
          estimated_sell_volume: 45000,
        },
      ]),
    );

    renderWithLanguage(<AlertsPanel symbol="SPY" orientation="vertical" />);

    // Scoped to the dedicated .alert-dominant element, not a whole-card
    // text match -- "Compra" is also a substring of the unrelated
    // "Compra/venta estimado (BVC)" caption on every card.
    const cards = await screen.findAllByRole("article");
    expect(cards[0].querySelector(".alert-dominant")).toHaveTextContent("Compra");
    expect(cards[1].querySelector(".alert-dominant")).toHaveTextContent("Venta");
  });

  it("labels an exact 50/50 split Mixto, not Compra or Venta (documented no-signal fallback, not a real tie)", async () => {
    apiMocks.getAlerts.mockResolvedValue(
      alertsResponse("SPY", [
        {
          symbol: "SPY",
          contract: "SPY260220C00540000",
          type: "UNUSUAL",
          amount: 45000,
          timestamp: "2026-08-03T14:00:00Z",
          estimated_buy_volume: 22500,
          estimated_sell_volume: 22500,
        },
      ]),
    );

    renderWithLanguage(<AlertsPanel symbol="SPY" orientation="vertical" />);

    const card = await screen.findByRole("article");
    expect(card.querySelector(".alert-dominant")).toHaveTextContent("Mixto");
  });

  it("keeps many alerts scrollable within the column, not the whole page", async () => {
    const manyAlerts = Array.from({ length: 30 }, (_, index) => ({
      symbol: "SPY",
      contract: `SPY260220P0054${String(index).padStart(4, "0")}`,
      type: (index % 2 === 0 ? "WHALE" : "UNUSUAL") as "WHALE" | "UNUSUAL",
      amount: 150_000 + index,
      timestamp: new Date(Date.UTC(2026, 7, 3, 14, 30, index)).toISOString(),
      estimated_buy_volume: 75_000,
      estimated_sell_volume: 75_000,
    }));
    apiMocks.getAlerts.mockResolvedValue(alertsResponse("SPY", manyAlerts));

    const { container } = renderWithLanguage(
      <AlertsPanel symbol="SPY" orientation="vertical" />,
    );

    await waitFor(() => {
      const column = container.querySelector(".alerts-column");
      expect(column).toBeInTheDocument();
      expect(column?.querySelectorAll(".alert-card")).toHaveLength(30);
    });
    // The scrollable hook is the column itself, not a page-level container
    // — same CSS class dashboard.test.tsx already confirms carries
    // overflow-y: auto, scoping the scroll to the panel.
    expect(container.querySelector(".alerts-panel-vertical > .alerts-column")).toBeInTheDocument();
  });

  it("surfaces a request failure as a translated alert message, not the raw error", async () => {
    // Regression check for the language-selector PR: `getJson` (lib/api.ts)
    // always threw a real `Error`, so `reason.message` always won over the
    // Spanish fallback text and users saw this raw string on screen. Now
    // every catch translates via `describeError` instead of reading
    // `.message` — confirm the friendly, translated text renders, not this.
    apiMocks.getAlerts.mockRejectedValue(new ApiError(500));

    renderWithLanguage(<AlertsPanel symbol="SPY" />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("No se pudo completar la solicitud. Intenta de nuevo.");
    expect(alert).not.toHaveTextContent("API request failed");
  });

  it("owns its own Whale Alerts thresholds gear button, opening and closing the panel", async () => {
    // Relocated here from the topbar (dashboard-spec.md section 23) so it
    // reads as this panel's own setting, not Gamma's — same
    // self-contained trigger+state+modal pattern as QuickScreener's own
    // settings gear.
    const user = userEvent.setup();
    renderWithLanguage(<AlertsPanel symbol="SPY" orientation="vertical" />);

    expect(screen.queryByText("Umbrales de Whale Alerts")).not.toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Configuración de umbrales de Whale Alerts" }),
    );

    expect(await screen.findByText("Umbrales de Whale Alerts")).toBeInTheDocument();
    await waitFor(() => expect(apiMocks.getWhaleThresholds).toHaveBeenCalled());

    await user.click(screen.getByRole("button", { name: "Cerrar" }));
    expect(screen.queryByText("Umbrales de Whale Alerts")).not.toBeInTheDocument();
  });
});
