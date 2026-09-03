import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { GammaResponse, MarketResponse } from "@/lib/types";
import { derivedMetricsFixture } from "@/test/fixtures";
import { GravityMap } from "./gravity-map";

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

function gamma(absoluteGammaStrike: number): GammaResponse {
  return {
    schema_version: 1,
    symbol: "SPY",
    as_of: "2026-08-03T14:30:00Z",
    gamma_flip: 548.5,
    call_wall: 560,
    put_wall: 540,
    absolute_gamma_strike: absoluteGammaStrike,
    max_pain: 549,
    net_gamma: 1,
    vega_exposure: 2,
    theta_exposure: 3,
    charm_exposure: 4,
    vanna_exposure: 5,
    dealer_position: "short_gamma",
    derived_metrics: derivedMetricsFixture,
  };
}

describe("GravityMap", () => {
  it("renders walls, current price and separate levels outside the 2% threshold", () => {
    render(<GravityMap gamma={gamma(550)} market={market} />);

    expect(screen.getByText("Put Wall")).toBeInTheDocument();
    expect(screen.getByText("Call Wall")).toBeInTheDocument();
    expect(screen.getByText("Gamma Flip")).toBeInTheDocument();
    expect(screen.getByText("Abs. Gamma")).toBeInTheDocument();
    expect(screen.getByText("Precio 549.1")).toBeInTheDocument();
    expect(screen.queryByText(/Max Pain/i)).not.toBeInTheDocument();
  });

  it("merges Flip and Absolute Gamma inside the 2% threshold", () => {
    render(<GravityMap gamma={gamma(548.7)} market={market} />);

    expect(screen.getByText("Flip / Abs. Gamma")).toBeInTheDocument();
    expect(screen.queryByText("Gamma Flip")).not.toBeInTheDocument();
  });

  it("skips the Gamma Flip marker instead of crashing when gamma_flip is null", () => {
    // gamma_flip=null is a legitimate "no sign crossing found" value, not
    // an error -- level()'s .toLocaleString() call throws on null (see
    // price-chart.tsx's own containment fix), so this must render without
    // it rather than crash the whole panel. The API type still says
    // `number` (see lib/types.ts) pending the coordinated design decision,
    // so this is the real runtime shape despite the declared type.
    const gammaWithNoFlip: GammaResponse = { ...gamma(550), gamma_flip: null as unknown as number };

    render(<GravityMap gamma={gammaWithNoFlip} market={market} />);

    expect(screen.queryByText("Gamma Flip")).not.toBeInTheDocument();
    expect(screen.queryByText("Flip / Abs. Gamma")).not.toBeInTheDocument();
    expect(screen.getByText("Put Wall")).toBeInTheDocument();
    expect(screen.getByText("Call Wall")).toBeInTheDocument();
    expect(screen.getByText("Abs. Gamma")).toBeInTheDocument();
    expect(screen.getByText("Precio 549.1")).toBeInTheDocument();
  });
});
