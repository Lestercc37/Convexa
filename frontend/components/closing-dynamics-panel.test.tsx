import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
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
    renderWithLanguage(<ClosingDynamicsPanel closingDynamics={activeClosingDynamics} />);

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

  it("always shows a visible note clarifying Magnet Strike is a reference level, not a predicted move (regression)", () => {
    // Confirmed live, 2026-09-16: Lester mentally subtracted spot price
    // minus the bare magnet strike number shown here and misread the
    // result as "expected move toward close" -- the note must be
    // visible by default (not hover-only) so it isn't missed the same
    // way again.
    renderWithLanguage(<ClosingDynamicsPanel closingDynamics={activeClosingDynamics} />);

    const note = screen.getByText(
      "Nivel de precio de referencia (mayor concentración de gamma) — no es una predicción de cuánto se moverá el precio.",
    );
    expect(note).toBeInTheDocument();
    expect(note).toHaveAttribute("title");
  });

  it("shows the distance in points from the live price to the magnet strike, correctly labeled above/below", () => {
    renderWithLanguage(
      <ClosingDynamicsPanel closingDynamics={activeClosingDynamics} spotPrice={545.5} />,
    );

    // magnet_strike=550, spotPrice=545.5 -> magnet sits 4.5 pts above.
    expect(screen.getByText("4.50 pts por encima del precio actual")).toBeInTheDocument();
  });

  it("labels the distance as below when the magnet strike sits under the live price", () => {
    renderWithLanguage(
      <ClosingDynamicsPanel closingDynamics={activeClosingDynamics} spotPrice={561} />,
    );

    // magnet_strike=550, spotPrice=561 -> magnet sits 11 pts below.
    expect(screen.getByText("11.00 pts por debajo del precio actual")).toBeInTheDocument();
  });

  it("omits the distance note when spotPrice isn't provided, or when magnet_strike is null", () => {
    const { rerender } = renderWithLanguage(
      <ClosingDynamicsPanel closingDynamics={activeClosingDynamics} />,
    );
    expect(screen.queryByText(/pts (por encima|por debajo)/)).not.toBeInTheDocument();

    rerender(
      <ClosingDynamicsPanel
        closingDynamics={{ ...activeClosingDynamics, magnet_strike: null }}
        spotPrice={545.5}
      />,
    );
    expect(screen.queryByText(/pts (por encima|por debajo)/)).not.toBeInTheDocument();
  });

  it("renders nothing when closing_dynamics is absent from the poll", () => {
    const { container } = renderWithLanguage(<ClosingDynamicsPanel closingDynamics={undefined} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when closing_dynamics is present but not active (outside the closing window)", () => {
    const { container } = renderWithLanguage(
      <ClosingDynamicsPanel closingDynamics={{ ...activeClosingDynamics, active: false }} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("shows neutral labels and a dash for magnet strike without breaking the render", () => {
    renderWithLanguage(
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
