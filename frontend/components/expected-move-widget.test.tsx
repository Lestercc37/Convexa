import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ExpectedMoveWidget } from "./expected-move-widget";

const expectedMove = {
  implied_1sd_dollars: 2.18,
  implied_1sd_pct: 0.37,
  remaining_1sd_dollars: 1.05,
  remaining_1sd_pct: 0.18,
  upper_bound: 591.47,
  lower_bound: 589.37,
  atm_iv: 0.123,
};

describe("ExpectedMoveWidget", () => {
  it("renders the expected range and remaining session move", () => {
    render(<ExpectedMoveWidget expectedMove={expectedMove} />);

    expect(
      screen.getByText(
        "Movimiento esperado: ±$2.18 (0.37%) — Rango: $589.37 – $591.47",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Remanente del día: ±$1.05 (0.18%)")).toBeInTheDocument();
  });

  it("does not break its parent when expected move is absent", () => {
    const { container } = render(<ExpectedMoveWidget />);

    expect(container).toBeEmptyDOMElement();
  });
});
