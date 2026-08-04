import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { DerivedMetrics } from "@/lib/types";
import { DerivedMetricsBar } from "./derived-metrics-bar";

const confirmedMetrics: DerivedMetrics = {
  dealer_impact_score: { value: 78, provisional: false, days_accumulated: 60 },
  signal_alignment_score: { value: 62, provisional: false, days_accumulated: 60 },
  market_bias: {
    score: 71.2,
    label: "bullish",
    provisional: false,
    days_accumulated: 60,
  },
  volatility_regime: {
    iv_rank: 42.5,
    label: "moderate",
    provisional: false,
    days_accumulated: 60,
  },
};

const proprietaryNote = "métrica propia de Convexa, no un estándar de mercado";

describe("DerivedMetricsBar", () => {
  it("renders all four confirmed metrics with their mandatory presentation notes", () => {
    render(<DerivedMetricsBar metrics={confirmedMetrics} />);

    expect(screen.getByText("Dealer Impact Score")).toBeInTheDocument();
    expect(screen.getByText("78")).toBeInTheDocument();
    expect(screen.getByText("Signal Alignment Score")).toBeInTheDocument();
    expect(screen.getByText("62")).toBeInTheDocument();
    expect(screen.getByText("Bullish · 71.2")).toBeInTheDocument();
    expect(screen.getByText("Moderate · IV Rank 42.5")).toBeInTheDocument();
    expect(screen.getAllByText(proprietaryNote)).toHaveLength(3);
    expect(screen.getByText("Ventana: 60 días")).toBeInTheDocument();
    expect(screen.queryByText(/Acumulando datos/)).not.toBeInTheDocument();
  });

  it("keeps provisional Signal Alignment numeric and null metrics as dashes", () => {
    const provisionalMetrics: DerivedMetrics = {
      dealer_impact_score: { value: null, provisional: true, days_accumulated: 8 },
      signal_alignment_score: { value: 57.4, provisional: true, days_accumulated: 8 },
      market_bias: {
        score: null,
        label: null,
        provisional: true,
        days_accumulated: 8,
      },
      volatility_regime: {
        iv_rank: null,
        label: null,
        provisional: true,
        days_accumulated: 8,
      },
    };

    render(<DerivedMetricsBar metrics={provisionalMetrics} />);

    const bar = screen.getByLabelText("Métricas derivadas");
    expect(within(bar).getByText("57.4")).toBeInTheDocument();
    expect(within(bar).getAllByText("—")).toHaveLength(3);
    expect(within(bar).getAllByText("Acumulando datos — 8d/20d")).toHaveLength(4);
    expect(within(bar).getAllByText(proprietaryNote)).toHaveLength(3);
    expect(within(bar).getByText("Ventana: 60 días")).toBeInTheDocument();
  });
});
