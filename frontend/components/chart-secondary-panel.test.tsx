import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import type {
  GammaAggregateItem,
  GammaAggregateResponse,
  GammaResponse,
  WhaleAlert,
  WhaleAlertsResponse,
} from "@/lib/types";
import { derivedMetricsFixture } from "@/test/fixtures";
import { ChartSecondaryPanel } from "./chart-secondary-panel";

const apiMocks = vi.hoisted(() => ({ getGammaNearTermProfile: vi.fn(), getAlerts: vi.fn() }));

// Between the two fixture strikes (545/550) — a realistic spot price
// mid-chain, not coinciding with either strike.
const SPOT_PRICE = 547.25;

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, getGammaNearTermProfile: apiMocks.getGammaNearTermProfile, getAlerts: apiMocks.getAlerts };
});

// Distinct from every fixture strike (540-555) so wall/level assertions
// below are unambiguous about which strike each line points at.
const gamma: GammaResponse = {
  schema_version: 1,
  symbol: "SPY",
  as_of: "2026-08-07T20:30:00Z",
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
  delta_exposure: 6,
  dealer_position: "long_gamma",
  derived_metrics: derivedMetricsFixture,
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
        open_interest: 14000,
        volume: 6800,
      },
      {
        strike: 550,
        total_gamma_exposure: 200,
        call_gamma_exposure: 120,
        put_gamma_exposure: -80,
        net_gamma: 40,
        contract_count: 3,
        absolute_gamma: 40,
        open_interest: 9000,
        volume: 5200,
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
    open_interest: 1000,
    volume: 500,
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
    quote_unavailable: false,
    ...overrides,
  };
}

function alertsResponse(alerts: WhaleAlert[]): WhaleAlertsResponse {
  return { schema_version: 1, symbol: "SPY", alerts };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.getGammaNearTermProfile.mockResolvedValue(profile());
  apiMocks.getAlerts.mockResolvedValue(alertsResponse([]));
});

describe("ChartSecondaryPanel", () => {
  it("renders the GEX-by-strike view by default, with a bar per strike", async () => {
    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} gamma={gamma} />);

    await waitFor(() => expect(apiMocks.getGammaNearTermProfile).toHaveBeenCalledWith("SPY", expect.any(AbortSignal)));

    expect(await screen.findByLabelText("GEX por strike para SPY")).toBeInTheDocument();
    expect(screen.getByLabelText("Strike 545")).toBeInTheDocument();
    expect(screen.getByLabelText("Strike 550")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "GEX por Strike" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("draws a single net GEX bar per strike, colored green when positive and red when negative", async () => {
    apiMocks.getGammaNearTermProfile.mockResolvedValue(
      profile({
        items: [
          {
            strike: 545,
            total_gamma_exposure: 390,
            call_gamma_exposure: 240,
            put_gamma_exposure: -150,
            net_gamma: 90,
            contract_count: 2,
            absolute_gamma: 90,
            open_interest: 14000,
            volume: 6800,
          },
          // The spec's own concrete example: Call GEX +50M, Put GEX -80M
          // -> a single -30M bar, red.
          {
            strike: 550,
            total_gamma_exposure: 130,
            call_gamma_exposure: 50,
            put_gamma_exposure: -80,
            net_gamma: -30,
            contract_count: 3,
            absolute_gamma: 30,
            open_interest: 9000,
            volume: 5200,
          },
        ],
      }),
    );
    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} gamma={gamma} />);
    await screen.findByLabelText("GEX por strike para SPY");

    // Exactly one bar per strike now — no separate call/put rects.
    expect(document.querySelectorAll(".secondary-gex-bar")).toHaveLength(2);

    const zeroLine = document.querySelector(".secondary-gex-zero");
    const zeroY = Number(zeroLine?.getAttribute("y1"));
    expect(Number.isNaN(zeroY)).toBe(false);

    const bar545 = screen.getByLabelText("Strike 545").querySelector(".secondary-gex-bar");
    expect(bar545).toHaveClass("positive");
    expect(bar545).not.toHaveClass("negative");
    const y545 = Number(bar545?.getAttribute("y"));
    const height545 = Number(bar545?.getAttribute("height"));
    // Positive net GEX draws upward from the zero line: bottom edge sits
    // on it, top edge is strictly above (a smaller SVG y coordinate).
    expect(y545 + height545).toBeCloseTo(zeroY, 5);
    expect(y545).toBeLessThan(zeroY);
    expect(height545).toBeGreaterThan(0);

    const bar550 = screen.getByLabelText("Strike 550").querySelector(".secondary-gex-bar");
    expect(bar550).toHaveClass("negative");
    expect(bar550).not.toHaveClass("positive");
    const y550 = Number(bar550?.getAttribute("y"));
    const height550 = Number(bar550?.getAttribute("height"));
    // Negative net GEX draws downward from the zero line: top edge sits
    // on it, extends to a strictly larger y coordinate.
    expect(y550).toBeCloseTo(zeroY, 5);
    expect(height550).toBeGreaterThan(0);
  });

  it("renders a zero-height, neutrally-classed bar when net GEX is exactly zero", async () => {
    apiMocks.getGammaNearTermProfile.mockResolvedValue(
      profile({
        items: [
          {
            // Real open interest on both sides that happens to net to
            // exactly zero (not "no interest") -- must still render, see
            // gexItems' own P-E filtering comment for why open_interest,
            // not net_gamma, is what that filter keys on.
            strike: 545,
            total_gamma_exposure: 100,
            call_gamma_exposure: 50,
            put_gamma_exposure: -50,
            net_gamma: 0,
            contract_count: 2,
            absolute_gamma: 0,
            open_interest: 8000,
            volume: 3000,
          },
          {
            strike: 550,
            total_gamma_exposure: 200,
            call_gamma_exposure: 120,
            put_gamma_exposure: -80,
            net_gamma: 40,
            contract_count: 3,
            absolute_gamma: 40,
            open_interest: 9000,
            volume: 5200,
          },
        ],
      }),
    );
    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} gamma={gamma} />);
    await screen.findByLabelText("GEX por strike para SPY");

    const bar545 = screen.getByLabelText("Strike 545").querySelector(".secondary-gex-bar");
    expect(bar545).toHaveClass("zero");
    expect(bar545).not.toHaveClass("positive");
    expect(bar545).not.toHaveClass("negative");
    expect(Number(bar545?.getAttribute("height"))).toBe(0);
  });

  it("excludes zero-open-interest strikes so far OTM/illiquid strikes can't squeeze the real ones into a narrow band (regression, P-E, 2026-09-21)", async () => {
    // Confirmed live, 2026-09-21: since the P1 rollout widened every
    // symbol's fetch to every expiration, `profile.items` can include far
    // OTM/far-dated strikes with zero real open interest -- their bar was
    // already invisible (net_gamma is exactly 0 whenever open_interest is
    // 0, since dealer exposure is gamma * open_interest * ...), but they
    // still stretched the x-axis range, squeezing every strike that
    // actually has a visible bar into a narrow band in the plot's middle.
    apiMocks.getGammaNearTermProfile.mockResolvedValue(
      profile({
        items: [
          {
            strike: 545,
            total_gamma_exposure: 390,
            call_gamma_exposure: 240,
            put_gamma_exposure: -150,
            net_gamma: 90,
            contract_count: 2,
            absolute_gamma: 90,
            open_interest: 14000,
            volume: 6800,
          },
          {
            strike: 550,
            total_gamma_exposure: 200,
            call_gamma_exposure: 120,
            put_gamma_exposure: -80,
            net_gamma: 40,
            contract_count: 3,
            absolute_gamma: 40,
            open_interest: 9000,
            volume: 5200,
          },
          // A far, illiquid strike from a distant expiration -- exactly
          // the shape of data P1 added. Zero real interest, zero net
          // gamma, nothing visible to lose by excluding it.
          {
            strike: 900,
            total_gamma_exposure: 0,
            call_gamma_exposure: 0,
            put_gamma_exposure: 0,
            net_gamma: 0,
            contract_count: 1,
            absolute_gamma: 0,
            open_interest: 0,
            volume: 0,
          },
        ],
      }),
    );
    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} gamma={gamma} />);
    await screen.findByLabelText("GEX por strike para SPY");

    expect(document.querySelectorAll(".secondary-gex-bar")).toHaveLength(2);
    expect(screen.getByLabelText("Strike 545")).toBeInTheDocument();
    expect(screen.getByLabelText("Strike 550")).toBeInTheDocument();
    expect(screen.queryByLabelText("Strike 900")).not.toBeInTheDocument();
  });

  it("falls back to every strike when none has real open interest yet, instead of rendering nothing", async () => {
    // Right after startup/a symbol switch, the chain can briefly still be
    // unenriched (every item's open_interest genuinely 0) -- excluding
    // everything in that moment would show an empty chart instead of the
    // real, if imprecise, data already on hand.
    apiMocks.getGammaNearTermProfile.mockResolvedValue(
      profile({
        items: [
          {
            strike: 545,
            total_gamma_exposure: 0,
            call_gamma_exposure: 0,
            put_gamma_exposure: 0,
            net_gamma: 0,
            contract_count: 2,
            absolute_gamma: 0,
            open_interest: 0,
            volume: 0,
          },
          {
            strike: 550,
            total_gamma_exposure: 0,
            call_gamma_exposure: 0,
            put_gamma_exposure: 0,
            net_gamma: 0,
            contract_count: 3,
            absolute_gamma: 0,
            open_interest: 0,
            volume: 0,
          },
        ],
      }),
    );
    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} gamma={gamma} />);
    await screen.findByLabelText("GEX por strike para SPY");

    expect(document.querySelectorAll(".secondary-gex-bar")).toHaveLength(2);
  });

  it("scales net bars on one shared axis, not independently per strike", async () => {
    // Strike 545 (net 90) has a much larger magnitude than strike 550
    // (net 40) — on a shared scale the 545 bar must be taller. Independent
    // per-strike normalization would make every bar the same height
    // regardless of its real magnitude, which this disproves.
    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} gamma={gamma} />);
    await screen.findByLabelText("GEX por strike para SPY");

    const height545 = Number(
      screen.getByLabelText("Strike 545").querySelector(".secondary-gex-bar")?.getAttribute("height"),
    );
    const height550 = Number(
      screen.getByLabelText("Strike 550").querySelector(".secondary-gex-bar")?.getAttribute("height"),
    );

    expect(height545).toBeGreaterThan(height550);
  });

  it("keeps Call GEX, Put GEX and Net GEX in the per-strike tooltip even though the bar itself is a single net value", async () => {
    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} gamma={gamma} />);
    await screen.findByLabelText("GEX por strike para SPY");

    const tooltip = screen.getByLabelText("Strike 545").querySelector("title");
    expect(tooltip?.textContent).toContain("Strike 545");
    expect(tooltip?.textContent).toContain("Call GEX: 240");
    expect(tooltip?.textContent).toContain("Put GEX: -150");
    expect(tooltip?.textContent).toContain("Net GEX: 90");
  });

  it("renders Call Wall, Put Wall, Abs. Gamma/Magnet, Max Pain and Gamma Flip as vertical lines at their real backend values", async () => {
    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} gamma={gamma} />);
    await screen.findByLabelText("GEX por strike para SPY");

    const callWall = screen.getByLabelText("Call Wall 555");
    const putWall = screen.getByLabelText("Put Wall 540");
    const absGamma = screen.getByLabelText("Abs. Gamma / Magnet 550");
    const maxPain = screen.getByLabelText("Max Pain 549");
    const gammaFlip = screen.getByLabelText("Gamma Flip 548.5");

    expect(callWall.querySelector("line")).toHaveClass("call-wall");
    expect(putWall.querySelector("line")).toHaveClass("put-wall");
    expect(absGamma.querySelector("line")).toHaveClass("abs-gamma");
    expect(maxPain.querySelector("line")).toHaveClass("max-pain");
    expect(gammaFlip.querySelector("line")).toHaveClass("gamma-flip");

    const callWallX = Number(callWall.querySelector("line")?.getAttribute("x1"));
    const putWallX = Number(putWall.querySelector("line")?.getAttribute("x1"));
    const absGammaX = Number(absGamma.querySelector("line")?.getAttribute("x1"));
    const maxPainX = Number(maxPain.querySelector("line")?.getAttribute("x1"));
    const gammaFlipX = Number(gammaFlip.querySelector("line")?.getAttribute("x1"));

    // Higher strikes sit further right on the X axis: Call Wall (555) >
    // Abs. Gamma (550) > Max Pain (549) > Gamma Flip (548.5) > Put Wall (540).
    expect(callWallX).toBeGreaterThan(absGammaX);
    expect(absGammaX).toBeGreaterThan(maxPainX);
    expect(maxPainX).toBeGreaterThan(gammaFlipX);
    expect(gammaFlipX).toBeGreaterThan(putWallX);
  });

  it("does not render a separate Magnet Strike line — confirmed the same backend value as Abs. Gamma, so one line covers both labels", async () => {
    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} gamma={gamma} />);
    await screen.findByLabelText("GEX por strike para SPY");

    expect(screen.queryByLabelText(/^Magnet Strike/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Abs. Gamma / Magnet 550")).toBeInTheDocument();
  });

  it("hides the Gamma Flip level line when gamma_flip is null, without hiding the others", async () => {
    renderWithLanguage(
      <ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} gamma={{ ...gamma, gamma_flip: null }} />,
    );
    await screen.findByLabelText("GEX por strike para SPY");

    expect(screen.queryByLabelText(/^Gamma Flip/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Max Pain 549")).toBeInTheDocument();
    expect(screen.getByLabelText("Call Wall 555")).toBeInTheDocument();
  });

  it("renders a spot price reference line and label, updating when the price prop changes", async () => {
    const { rerender } = renderWithLanguage(
      <ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} gamma={gamma} />,
    );
    await screen.findByLabelText("GEX por strike para SPY");

    expect(screen.getByLabelText("Precio spot: 547.25")).toBeInTheDocument();
    const spotLine = document.querySelector(".secondary-gex-spot");
    const initialX = spotLine?.getAttribute("x1");
    expect(initialX).toBeTruthy();
    expect(screen.getByText("547.25")).toBeInTheDocument();

    rerender(<ChartSecondaryPanel symbol="SPY" spotPrice={549.8} gamma={gamma} />);

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
    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={999} gamma={gamma} />);
    await screen.findByLabelText("GEX por strike para SPY");

    const spotLine = document.querySelector(".secondary-gex-spot");
    const x = Number(spotLine?.getAttribute("x1"));
    expect(x).toBeLessThanOrEqual(740); // GEX_PLOT.right
    expect(x).toBeGreaterThanOrEqual(30); // GEX_PLOT.left
  });

  it("toggles from the GEX view to the Whale Alerts flow view and back", async () => {
    const user = userEvent.setup();
    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} gamma={gamma} />);
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
    apiMocks.getGammaNearTermProfile.mockRejectedValue(new ApiError(404));

    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} gamma={gamma} />);

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

    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} gamma={gamma} />);
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

  it("keeps both alerts when the same contract+timestamp trips two alert types, instead of the Map silently dropping one (regression)", async () => {
    // Confirmed live, 2026-09: a single reading can independently trip a
    // magnitude threshold (WHALE/UNUSUAL) *and* the separate sustained-
    // flow window, so the same symbol+contract+timestamp legitimately
    // carries two distinct alerts with a different `type`. Before this
    // fix, alertKey() (symbol+contract+timestamp only) collided for both,
    // and since it's used as this component's Map key
    // (`alertsByKey.set(alertKey(alert), alert)`), the second alert
    // silently overwrote the first -- no warning, no error, just one
    // fewer point on the flow line than alerts actually received.
    const whaleAlert = alert({ type: "WHALE", estimated_buy_volume: 1500, estimated_sell_volume: 500 });
    const sustainedAlert = alert({
      type: "SUSTAINED_FLOW",
      estimated_buy_volume: 300,
      estimated_sell_volume: 900,
    });
    apiMocks.getAlerts.mockResolvedValue(alertsResponse([whaleAlert, sustainedAlert]));

    const user = userEvent.setup();
    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} gamma={gamma} />);
    await user.click(screen.getByRole("button", { name: "Flujo Whale Alerts" }));

    await waitFor(() => expect(apiMocks.getAlerts).toHaveBeenCalledTimes(1));
    await waitFor(() => {
      const points = document
        .querySelector(".secondary-flow-line")
        ?.getAttribute("points")
        ?.trim()
        .split(/\s+/);
      // Both alerts received (2), both must survive into the Map -- 1
      // would mean the second silently overwrote the first.
      expect(points).toHaveLength(2);
    });
  });

  it("shows the loading state before the first alerts poll resolves", async () => {
    let resolveAlerts: (value: WhaleAlertsResponse) => void = () => {};
    apiMocks.getAlerts.mockImplementation(
      () => new Promise((resolve) => { resolveAlerts = resolve; }),
    );
    const user = userEvent.setup();

    renderWithLanguage(<ChartSecondaryPanel symbol="SPY" spotPrice={SPOT_PRICE} gamma={gamma} />);
    await user.click(screen.getByRole("button", { name: "Flujo Whale Alerts" }));

    expect(screen.getByText("Cargando flujo de Whale Alerts…")).toBeInTheDocument();

    resolveAlerts(alertsResponse([]));
    expect(await screen.findByText("Sin alertas todavía en esta sesión.")).toBeInTheDocument();
  });

  it("renders a bar for every strike but thins labels once there are too many to fit legibly", async () => {
    const items = manyStrikeItems(32, 7555, 5);
    apiMocks.getGammaNearTermProfile.mockResolvedValue(profile({ symbol: "SPX", items }));

    // 7557 is closest to strike 7555 (index 0), which the label-thinning
    // step (2, at these 32 strikes) already keeps on its own — isolates
    // this test to the thinning behavior itself, not the "always show
    // nearest" override (covered separately below).
    renderWithLanguage(<ChartSecondaryPanel symbol="SPX" spotPrice={7557} gamma={gamma} />);
    await screen.findByLabelText("GEX por strike para SPX");

    expect(document.querySelectorAll(".secondary-gex-bar")).toHaveLength(32);

    const labels = document.querySelectorAll(".secondary-gex-strike-label");
    expect(labels.length).toBeGreaterThan(0);
    expect(labels.length).toBeLessThan(32);
    expect(labels).toHaveLength(16);
  });

  it("always labels the strike closest to spot even when the thinning pattern would skip it", async () => {
    const items = manyStrikeItems(32, 7555, 5);
    apiMocks.getGammaNearTermProfile.mockResolvedValue(profile({ symbol: "SPX", items }));

    // Strike 7,560 is index 1 (odd) — the computed step (2) at these 32
    // strikes only labels even indices, so this strike would be skipped
    // by the pattern alone. 7561 is closest to it.
    renderWithLanguage(<ChartSecondaryPanel symbol="SPX" spotPrice={7561} gamma={gamma} />);
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
    apiMocks.getGammaNearTermProfile.mockResolvedValue(profile({ symbol: "SPX", items }));

    renderWithLanguage(<ChartSecondaryPanel symbol="SPX" spotPrice={7561} gamma={gamma} />);
    await screen.findByLabelText("GEX por strike para SPX");

    expect(screen.getByText("7,560")).toBeInTheDocument();
    expect(screen.queryByText("7,555")).not.toBeInTheDocument();
    expect(screen.queryByText("7,565")).not.toBeInTheDocument();
    // 16 from the pattern minus the two suppressed neighbors, plus the
    // one forced label: 16 - 2 + 1 = 15.
    expect(document.querySelectorAll(".secondary-gex-strike-label")).toHaveLength(15);
  });
});
