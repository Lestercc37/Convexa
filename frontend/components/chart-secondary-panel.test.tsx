import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import type { GammaAggregateItem, GammaAggregateResponse, WhaleAlert, WhaleAlertsResponse } from "@/lib/types";
import { ChartSecondaryPanel } from "./chart-secondary-panel";

const apiMocks = vi.hoisted(() => ({ getGammaProfile: vi.fn(), getAlerts: vi.fn() }));

// Between the two fixture strikes (545/550) — a realistic spot price
// mid-chain, not coinciding with either strike.
const SPOT_PRICE = 547.25;

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

// Mirrors a real SPX snapshot observed in production after the
// ATR-anchored width shipped (PR #84): 32 strikes, $5 apart, starting
// at 7555 — the scenario that first exposed the strike-label overlap.
function manyStrikeItems(count: number, start: number, step: number): GammaAggregateItem[] {
  return Array.from({ length: count }, (_, index) => ({
    strike: start + index * step,
    total_gamma_exposure: 300,
    call_gamma_exposure: 200,
    put_gamma_exposure: -100,
    net_gamma: 100,
    contract_count: 2,
    absolute_gamma: 100,
  }));
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
    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} />);

    await waitFor(() => expect(apiMocks.getGammaProfile).toHaveBeenCalledWith("SPY", expect.any(AbortSignal)));

    expect(await screen.findByLabelText("GEX por strike para SPY")).toBeInTheDocument();
    expect(screen.getByLabelText("Strike 545")).toBeInTheDocument();
    expect(screen.getByLabelText("Strike 550")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "GEX por Strike" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("draws call bars rising above the zero line and put bars falling below it", async () => {
    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} />);
    await screen.findByLabelText("GEX por strike para SPY");

    const zeroLine = document.querySelector(".secondary-gex-zero");
    const zeroY = Number(zeroLine?.getAttribute("y1"));
    expect(Number.isNaN(zeroY)).toBe(false);

    const strike545 = screen.getByLabelText("Strike 545");
    const callBar = strike545.querySelector(".secondary-gex-bar.call");
    const putBar = strike545.querySelector(".secondary-gex-bar.put");
    const callY = Number(callBar?.getAttribute("y"));
    const callHeight = Number(callBar?.getAttribute("height"));
    const putY = Number(putBar?.getAttribute("y"));
    const putHeight = Number(putBar?.getAttribute("height"));

    // A positive call_gamma_exposure (240) draws upward from the zero
    // line: its bottom edge (y + height) sits at the zero line, and its
    // top edge is strictly above it (a smaller SVG y coordinate).
    expect(callY + callHeight).toBeCloseTo(zeroY, 5);
    expect(callY).toBeLessThan(zeroY);
    expect(callHeight).toBeGreaterThan(0);

    // A negative put_gamma_exposure (-150) draws downward from the zero
    // line: its top edge starts at the zero line and it extends to a
    // strictly larger y coordinate.
    expect(putY).toBeCloseTo(zeroY, 5);
    expect(putHeight).toBeGreaterThan(0);
  });

  it("scales both directions on one shared axis, not independently per side", async () => {
    // Strike 545 (call 240, put -150) has a much larger call than strike
    // 550 (call 120, put -80) — on a shared scale the 545 call bar must
    // be taller than the 550 call bar. Independent per-side normalization
    // would make every call bar the same height regardless of its real
    // magnitude, which this disproves.
    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} />);
    await screen.findByLabelText("GEX por strike para SPY");

    const call545Height = Number(
      screen.getByLabelText("Strike 545").querySelector(".secondary-gex-bar.call")?.getAttribute("height"),
    );
    const call550Height = Number(
      screen.getByLabelText("Strike 550").querySelector(".secondary-gex-bar.call")?.getAttribute("height"),
    );

    expect(call545Height).toBeGreaterThan(call550Height);
  });

  it("renders a spot price reference line and label, updating when the price prop changes", async () => {
    const { rerender } = renderWithLanguage(
      <ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} />,
    );
    await screen.findByLabelText("GEX por strike para SPY");

    expect(screen.getByLabelText("Precio spot: 547.25")).toBeInTheDocument();
    const spotLine = document.querySelector(".secondary-gex-spot");
    const initialX = spotLine?.getAttribute("x1");
    expect(initialX).toBeTruthy();
    expect(screen.getByText("547.25")).toBeInTheDocument();

    rerender(<ChartSecondaryPanel symbol="SPY" spotPrice={549.8} />);

    expect(screen.getByLabelText("Precio spot: 549.8")).toBeInTheDocument();
    expect(screen.getByText("549.8")).toBeInTheDocument();
    const updatedLine = document.querySelector(".secondary-gex-spot");
    expect(updatedLine?.getAttribute("x1")).not.toBe(initialX);
  });

  it("clamps the spot price line inside the plot when the price sits outside the strike range", async () => {
    // Real scenario, reproduced live during manual testing: the fixture
    // strikes are 540-550, but the mock underlying's own spot price
    // (552.25) sits just outside that range — the line must stay inside
    // the visible plot instead of drifting past its right edge.
    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={999} />);
    await screen.findByLabelText("GEX por strike para SPY");

    const spotLine = document.querySelector(".secondary-gex-spot");
    const x = Number(spotLine?.getAttribute("x1"));
    expect(x).toBeLessThanOrEqual(740); // GEX_PLOT.right
    expect(x).toBeGreaterThanOrEqual(30); // GEX_PLOT.left
  });

  it("toggles from the GEX view to the Whale Alerts flow view and back", async () => {
    const user = userEvent.setup();
    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} />);
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

    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} />);

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

    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} />);
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

    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} />);
    await user.click(screen.getByRole("button", { name: "Flujo Whale Alerts" }));

    expect(screen.getByText("Cargando flujo de Whale Alerts…")).toBeInTheDocument();

    resolveAlerts(alertsResponse([]));
    expect(await screen.findByText("Sin alertas todavía en esta sesión.")).toBeInTheDocument();
  });

  it("renders a bar for every strike but thins labels once there are too many to fit legibly", async () => {
    const items = manyStrikeItems(32, 7555, 5);
    apiMocks.getGammaProfile.mockResolvedValue(profile({ symbol: "SPX", items }));

    // 7557 is closest to strike 7555 (index 0), which the label-thinning
    // step (2, at these 32 strikes) already keeps on its own — isolates
    // this test to the thinning behavior itself, not the "always show
    // nearest" override (covered separately below).
    renderWithLanguage(<ChartSecondaryPanel symbol="SPX" spotPrice={7557} />);
    await screen.findByLabelText("GEX por strike para SPX");

    expect(document.querySelectorAll(".secondary-gex-bar.call")).toHaveLength(32);
    expect(document.querySelectorAll(".secondary-gex-bar.put")).toHaveLength(32);

    const labels = document.querySelectorAll(".secondary-gex-strike-label");
    expect(labels.length).toBeGreaterThan(0);
    expect(labels.length).toBeLessThan(32);
    expect(labels).toHaveLength(16);
  });

  it("always labels the strike closest to spot even when the thinning pattern would skip it", async () => {
    const items = manyStrikeItems(32, 7555, 5);
    apiMocks.getGammaProfile.mockResolvedValue(profile({ symbol: "SPX", items }));

    // Strike 7,560 is index 1 (odd) — the computed step (2) at these 32
    // strikes only labels even indices, so this strike would be skipped
    // by the pattern alone. 7561 is closest to it.
    renderWithLanguage(<ChartSecondaryPanel symbol="SPX" spotPrice={7561} />);
    await screen.findByLabelText("GEX por strike para SPX");

    expect(screen.getByText("7,560")).toBeInTheDocument();
  });

  it("suppresses a step-pattern label that would collide with the forced nearest-to-spot label", async () => {
    // Regression test for a real overlap caught by measuring actual
    // rendered label positions against a live SPX chain: strikes 7,555
    // (index 0) and 7,565 (index 2) are both on the step-2 pattern and
    // both sit within one step of index 1 (7,560, forced by the test
    // above) — showing all three visually overlapped in the browser.
    // Only the forced label should render in that neighborhood.
    const items = manyStrikeItems(32, 7555, 5);
    apiMocks.getGammaProfile.mockResolvedValue(profile({ symbol: "SPX", items }));

    renderWithLanguage(<ChartSecondaryPanel symbol="SPX" spotPrice={7561} />);
    await screen.findByLabelText("GEX por strike para SPX");

    expect(screen.getByText("7,560")).toBeInTheDocument();
    expect(screen.queryByText("7,555")).not.toBeInTheDocument();
    expect(screen.queryByText("7,565")).not.toBeInTheDocument();
    // 16 from the pattern minus the two suppressed neighbors, plus the
    // one forced label: 16 - 2 + 1 = 15.
    expect(document.querySelectorAll(".secondary-gex-strike-label")).toHaveLength(15);
  });
});
