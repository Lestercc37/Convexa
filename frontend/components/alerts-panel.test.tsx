import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import type { Underlying, WhaleAlertsResponse } from "@/lib/types";
import { AlertsPanel } from "./alerts-panel";

const apiMocks = vi.hoisted(() => ({
  getAlerts: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, ...apiMocks };
});

const underlyings: Underlying[] = [
  { symbol: "SPY", kind: "equity", is_priority: true },
  { symbol: "QQQ", kind: "equity", is_priority: true },
];

function alertsResponse(symbol: string, alerts: WhaleAlertsResponse["alerts"]): WhaleAlertsResponse {
  return { schema_version: 1, symbol, alerts };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AlertsPanel", () => {
  it("renders alert cards from every active underlying, most recent first", async () => {
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
              },
            ])
          : alertsResponse("QQQ", [
              {
                symbol: "QQQ",
                contract: "QQQ260220P00480000",
                type: "WHALE",
                amount: 210000,
                timestamp: "2026-08-03T14:05:00Z",
              },
            ]),
      ),
    );

    renderWithLanguage(<AlertsPanel underlyings={underlyings} />);

    await waitFor(() => expect(apiMocks.getAlerts).toHaveBeenCalledTimes(2));
    expect(apiMocks.getAlerts).toHaveBeenCalledWith("SPY", expect.any(AbortSignal));
    expect(apiMocks.getAlerts).toHaveBeenCalledWith("QQQ", expect.any(AbortSignal));

    const cards = await screen.findAllByRole("article");
    expect(cards).toHaveLength(2);
    // QQQ's alert (14:05) is more recent than SPY's (14:00) — sorted first.
    expect(cards[0]).toHaveTextContent("QQQ");
    expect(cards[0]).toHaveTextContent("Whale");
    expect(cards[0]).toHaveTextContent("$210,000");
    expect(cards[1]).toHaveTextContent("SPY");
    expect(cards[1]).toHaveTextContent("Unusual");
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
        },
      ]),
    );

    const { container: horizontalContainer } = renderWithLanguage(
      <AlertsPanel underlyings={[underlyings[0]]} />,
    );
    await screen.findAllByRole("article");
    expect(horizontalContainer.querySelector(".alerts-row")).toBeInTheDocument();
    expect(horizontalContainer.querySelector(".alerts-column")).not.toBeInTheDocument();

    const { container: verticalContainer } = renderWithLanguage(
      <AlertsPanel underlyings={[underlyings[0]]} orientation="vertical" />,
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

    renderWithLanguage(<AlertsPanel underlyings={[underlyings[0]]} />);

    await waitFor(() => expect(apiMocks.getAlerts).toHaveBeenCalled());
    expect(await screen.findByText("Sin alertas recientes.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  it("surfaces a request failure as a translated alert message, not the raw error", async () => {
    // Regression check for the language-selector PR: `getJson` (lib/api.ts)
    // always threw a real `Error`, so `reason.message` always won over the
    // Spanish fallback text and users saw this raw string on screen. Now
    // every catch translates via `describeError` instead of reading
    // `.message` — confirm the friendly, translated text renders, not this.
    apiMocks.getAlerts.mockRejectedValue(new ApiError(500));

    renderWithLanguage(<AlertsPanel underlyings={[underlyings[0]]} />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("No se pudo completar la solicitud. Intenta de nuevo.");
    expect(alert).not.toHaveTextContent("API request failed");
  });
});
