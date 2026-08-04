import type { DerivedMetrics } from "@/lib/types";

type DerivedMetricsBarProps = {
  metrics: DerivedMetrics;
};

type MetricCardProps = {
  name: string;
  value: string;
  note: string;
  provisional: boolean;
  daysAccumulated: number;
};

const CONVEXA_NOTE = "métrica propia de Convexa, no un estándar de mercado";
const VOLATILITY_NOTE = "Ventana: 60 días";

function formatNumber(value: number | null): string {
  return value === null ? "—" : value.toLocaleString("en-US", { maximumFractionDigits: 1 });
}

function labeledValue(label: string | null, value: number | null, prefix = ""): string {
  const formatted = formatNumber(value);
  if (label === null) return formatted;
  const readableLabel = `${label.charAt(0).toUpperCase()}${label.slice(1)}`;
  return `${readableLabel} · ${prefix}${formatted}`;
}

function MetricCard({
  name,
  value,
  note,
  provisional,
  daysAccumulated,
}: MetricCardProps) {
  return (
    <article className={`metric-card${provisional ? " provisional" : ""}`}>
      <span className="metric-name">{name}</span>
      <strong className="metric-value">{value}</strong>
      {provisional && (
        <span className="metric-progress">Acumulando datos — {daysAccumulated}d/20d</span>
      )}
      <span className="metric-note" title={note}>
        {note}
      </span>
    </article>
  );
}

export function DerivedMetricsBar({ metrics }: DerivedMetricsBarProps) {
  const dealerImpact = metrics.dealer_impact_score;
  const signalAlignment = metrics.signal_alignment_score;
  const marketBias = metrics.market_bias;
  const volatility = metrics.volatility_regime;

  return (
    <section className="panel metrics-bar" aria-label="Métricas derivadas">
      <MetricCard
        name="Dealer Impact Score"
        value={formatNumber(dealerImpact.value)}
        note={CONVEXA_NOTE}
        provisional={dealerImpact.provisional}
        daysAccumulated={dealerImpact.days_accumulated}
      />
      <MetricCard
        name="Signal Alignment Score"
        value={formatNumber(signalAlignment.value)}
        note={CONVEXA_NOTE}
        provisional={signalAlignment.provisional}
        daysAccumulated={signalAlignment.days_accumulated}
      />
      <MetricCard
        name="Market Bias"
        value={labeledValue(marketBias.label, marketBias.score)}
        note={CONVEXA_NOTE}
        provisional={marketBias.provisional}
        daysAccumulated={marketBias.days_accumulated}
      />
      <MetricCard
        name="Volatility Regime"
        value={labeledValue(volatility.label, volatility.iv_rank, "IV Rank ")}
        note={VOLATILITY_NOTE}
        provisional={volatility.provisional}
        daysAccumulated={volatility.days_accumulated}
      />
    </section>
  );
}
