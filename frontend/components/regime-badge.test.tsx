import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { GammaResponse, MarketResponse } from "@/lib/types";
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
};

const market: MarketResponse = {
  schema_version: 1,
  symbol: "SPY",
  as_of: "2026-08-03T14:30:05Z",
  price: 549.1,
  volume: 1_000_000,
};

describe("RegimeBadge", () => {
  it("renders the dealer regime and price relative to Gamma Flip", () => {
    render(<RegimeBadge gamma={gamma} market={market} />);

    expect(screen.getByRole("heading", { name: "LONG GAMMA" })).toBeInTheDocument();
    expect(screen.getByText(/SPY \$549\.10 — arriba del Flip \(\$548\.50\)/)).toBeInTheDocument();
  });
});
