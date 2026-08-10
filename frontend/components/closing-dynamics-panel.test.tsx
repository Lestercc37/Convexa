import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ClosingDynamics } from "@/lib/types";
import { ClosingDynamicsPanel } from "./closing-dynamics-panel";

const activeClosingDynamics: ClosingDynamics = {
  active: true,
  time_to_close_pct: 8.5,
  pin_score: 62,
  magnet_strike: 550,
  charm_regime: "time_decay_dealers_buy",
  vanna_interpretation: "iv_increase_dealers_sell",
  max_pain: 548,
};

describe("ClosingDynamicsPanel", () => {
  it("renders Pin Risk Score, magnet strike, and translated charm/vanna labels when active", () => {
    render(<ClosingDynamicsPanel closingDynamics={activeClosingDynamics} />);

    expect(screen.getByLabelText("Dinámica de Cierre")).toBeInTheDocument();
    expect(screen.getByText("62")).toBeInTheDocument();
    expect(screen.getByText("550")).toBeInTheDocument();
    expect(
      screen.getByText("El paso del tiempo empuja a los dealers a comprar"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Un aumento de volatilidad empujaría a los dealers a vender"),
    ).toBeInTheDocument();
    // No raw enum values leaked into the rendered text.
    expect(screen.queryByText(/time_decay_dealers_buy/)).not.toBeInTheDocument();
    expect(screen.queryByText(/iv_increase_dealers_sell/)).not.toBeInTheDocument();
    expect(
      screen.getByText("métrica propia de Convexa, no un estándar de mercado"),
    ).toBeInTheDocument();
    const meter = screen.getByRole("meter", { name: "Pin Risk Score" });
    expect(meter).toHaveAttribute("aria-valuenow", "62");
  });

  it("renders nothing when closing_dynamics is absent from the poll", () => {
    const { container } = render(<ClosingDynamicsPanel closingDynamics={undefined} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when closing_dynamics is present but not active (outside the closing window)", () => {
    const { container } = render(
      <ClosingDynamicsPanel closingDynamics={{ ...activeClosingDynamics, active: false }} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("shows neutral labels and a dash for magnet strike without breaking the render", () => {
    render(
      <ClosingDynamicsPanel
        closingDynamics={{
          ...activeClosingDynamics,
          magnet_strike: null,
          charm_regime: null,
          vanna_interpretation: null,
        }}
      />,
    );

    expect(screen.getByText("—")).toBeInTheDocument();
    expect(
      screen.getByText("Neutral — sin presión direccional por paso del tiempo"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Neutral — sin presión direccional por volatilidad"),
    ).toBeInTheDocument();
  });
});
