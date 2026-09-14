import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import type { GammaResponse } from "@/lib/types";
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

describe("RegimeBadge", () => {
  it("renders only the regime label and the total dollar gamma amount", () => {
    renderWithLanguage(
      <RegimeBadge gamma={{ ...gamma, dealer_position: "long_gamma", net_gamma: 16_580_000_000 }} />,
    );

    expect(screen.getByRole("heading", { name: "LONG GAMMA" })).toBeInTheDocument();
    expect(screen.getByText("+$16.6B")).toBeInTheDocument();
    expect(screen.getByLabelText("Régimen gamma")).toHaveClass("long");
  });

  it("renders SHORT GAMMA with a negative amount for a negative net_gamma", () => {
    renderWithLanguage(
      <RegimeBadge gamma={{ ...gamma, dealer_position: "short_gamma", net_gamma: -29_530_000 }} />,
    );

    expect(screen.getByRole("heading", { name: "SHORT GAMMA" })).toBeInTheDocument();
    expect(screen.getByText("-$29.5M")).toBeInTheDocument();
    expect(screen.getByLabelText("Régimen gamma")).toHaveClass("short");
  });

  it("derives the label from gamma.dealer_position, not from a market prop", () => {
    // RegimeBadge takes no `market` prop at all (removed 2026-09-14):
    // reading both the label and the amount off the same `gamma` object
    // makes them structurally unable to disagree -- see RegimeCompactBadge's
    // own test below, which documents the same reasoning for the pattern
    // this component now shares with it.
    renderWithLanguage(<RegimeBadge gamma={{ ...gamma, dealer_position: "long_gamma", net_gamma: 5 }} />);

    expect(screen.getByRole("heading", { name: "LONG GAMMA" })).toBeInTheDocument();
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
    // RegimeCompactBadge takes no `market` prop -- this test exists to
    // document why: reading both the label and its own dollar amount
    // (always net_gamma's real sign) off the same `gamma` object makes
    // them structurally unable to disagree, not just untested.
    renderWithLanguage(
      <RegimeCompactBadge gamma={{ ...gamma, dealer_position: "long_gamma", net_gamma: 5 }} />,
    );

    expect(screen.getByText(/^LONG GAMMA/)).toBeInTheDocument();
  });
});
