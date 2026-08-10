import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EagleAlertsResponse, Underlying } from "@/lib/types";
import { AlertsPanel } from "./alerts-panel";

const apiMocks = vi.hoisted(() => ({
  getAlerts: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

const underlyings: Underlying[] = [
  { symbol: "SPY", kind: "equity", is_priority: true },
  { symbol: "QQQ", kind: "equity", is_priority: true },
];

function alertsResponse(symbol: string, alerts: EagleAlertsResponse["alerts"]): EagleAlertsResponse {
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

    render(<AlertsPanel underlyings={underlyings} />);

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

  it("shows an empty state, without an error, when there are no alerts", async () => {
    apiMocks.getAlerts.mockResolvedValue(alertsResponse("SPY", []));

    render(<AlertsPanel underlyings={[underlyings[0]]} />);

    await waitFor(() => expect(apiMocks.getAlerts).toHaveBeenCalled());
    expect(await screen.findByText("Sin alertas recientes.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  it("surfaces a request failure as an alert message", async () => {
    apiMocks.getAlerts.mockRejectedValue(new Error("API request failed (500)"));

    render(<AlertsPanel underlyings={[underlyings[0]]} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("API request failed (500)");
  });
});
