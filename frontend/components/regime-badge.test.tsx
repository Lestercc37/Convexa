import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import type { GammaResponse, MarketResponse } from "@/lib/types";
import { derivedMetricsFixture } from "@/test/fixtures";
import { RegimeBadge, RegimeCompactBadge } from "./regime-badge";

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

const tooltip =
  "El precio cruzó el Gamma Flip antes del último recálculo del agregado — régimen basado en precio.";

describe("RegimeBadge", () => {
  it("renders the dealer regime and price relative to Gamma Flip", () => {
    renderWithLanguage(<RegimeBadge gamma={gamma} market={market} />);

    expect(screen.getByRole("heading", { name: "LONG GAMMA" })).toBeInTheDocument();
    expect(screen.getByText(/SPY \$549\.10 — arriba del Flip \(\$548\.50\)/)).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "Régimen transitorio" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Régimen gamma")).not.toHaveClass("unconfirmed");
    expect(screen.getByLabelText("Régimen gamma")).not.toHaveAttribute("title");
  });

  it("marks a price-resolved regime as transient with the documented tooltip", () => {
    renderWithLanguage(
      <RegimeBadge
        gamma={{ ...gamma, dealer_position: "short_gamma" }}
        market={{
          ...market,
          dealer_mode: "long_gamma",
          dealer_mode_source: "price_vs_flip",
          dealer_mode_confirmed: false,
        }}
      />,
    );

    expect(screen.getByRole("heading", { name: "LONG GAMMA" })).toBeInTheDocument();
    expect(screen.getByLabelText("Régimen gamma")).toHaveClass("unconfirmed");
    expect(screen.getByLabelText("Régimen gamma")).toHaveAttribute("title", tooltip);
    expect(screen.getByRole("img", { name: "Régimen transitorio" })).toHaveAttribute(
      "title",
      tooltip,
    );
  });
});

describe("RegimeCompactBadge", () => {
  it("renders LONG GAMMA with a compact, sign-explicit dollar amount for a positive net_gamma", () => {
    renderWithLanguage(
      <RegimeCompactBadge gamma={{ ...gamma, dealer_position: "long_gamma", net_gamma: 16_580_000_000 }} />,
    );

    expect(screen.getByText("LONG GAMMA +$16.6B")).toBeInTheDocument();
    expect(screen.getByLabelText("LONG GAMMA +$16.6B")).toHaveClass("long");
  });

  it("renders SHORT GAMMA with a negative amount for a negative net_gamma", () => {
    renderWithLanguage(
      <RegimeCompactBadge gamma={{ ...gamma, dealer_position: "short_gamma", net_gamma: -29_530_000 }} />,
    );

    expect(screen.getByText("SHORT GAMMA -$29.5M")).toBeInTheDocument();
    expect(screen.getByLabelText("SHORT GAMMA -$29.5M")).toHaveClass("short");
  });

  it("derives the label from gamma.dealer_position, not from a market prop", () => {
    // RegimeCompactBadge takes no `market` prop at all -- this test exists
    // to document why: RegimeBadge (above) can legitimately show a regime
    // that disagrees with dealer_position while "unconfirmed" (see the
    // price-vs-Gamma-Flip test above), but that would let this badge's
    // label and its own dollar amount (always net_gamma's real sign)
    // disagree with each other. Reading both off the same `gamma` object
    // makes that structurally impossible, not just untested.
    renderWithLanguage(
      <RegimeCompactBadge gamma={{ ...gamma, dealer_position: "long_gamma", net_gamma: 5 }} />,
    );

    expect(screen.getByText(/^LONG GAMMA/)).toBeInTheDocument();
  });
});
