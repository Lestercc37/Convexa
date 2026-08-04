import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { GammaResponse, MarketResponse } from "@/lib/types";
import { derivedMetricsFixture } from "@/test/fixtures";
import { RegimeBadge } from "./regime-badge";

const gamma: GammaResponse = {
  schema_version: 1,
  symbol: "SPY",
  as_of: "2026-08-03T14:30:00Z",
  gamma_flip: 548.5,
  call_wall: 555,
  put_wall: 540,
  absolute_gamma_strike: 550,
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
    render(<RegimeBadge gamma={gamma} market={market} />);

    expect(screen.getByRole("heading", { name: "LONG GAMMA" })).toBeInTheDocument();
    expect(screen.getByText(/SPY \$549\.10 — arriba del Flip \(\$548\.50\)/)).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "Régimen transitorio" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Régimen gamma")).not.toHaveClass("unconfirmed");
    expect(screen.getByLabelText("Régimen gamma")).not.toHaveAttribute("title");
  });

  it("marks a price-resolved regime as transient with the documented tooltip", () => {
    render(
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
