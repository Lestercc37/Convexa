import { useLanguage } from "@/lib/i18n/language-context";
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
  accumulatingLabel: (daysAccumulated: number) => string;
};

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
  accumulatingLabel,
}: MetricCardProps) {
  return (
    <article className={`metric-card${provisional ? " provisional" : ""}`}>
      <span className="metric-name">{name}</span>
      <strong className="metric-value">{value}</strong>
      {provisional && (
        <span className="metric-progress">{accumulatingLabel(daysAccumulated)}</span>
      )}
      <span className="metric-note" title={note}>
        {note}
      </span>
    </article>
  );
}

export function DerivedMetricsBar({ metrics }: DerivedMetricsBarProps) {
  const { t } = useLanguage();
  const dealerImpact = metrics.dealer_impact_score;
  const signalAlignment = metrics.signal_alignment_score;
  const marketBias = metrics.market_bias;
  const volatility = metrics.volatility_regime;
  const convexaNote = t.common.convexaNote;
  const accumulatingLabel = t.derivedMetricsBar.accumulating;

  return (
    <section className="panel metrics-bar" aria-label={t.derivedMetricsBar.ariaLabel}>
      <MetricCard
        name="Dealer Impact Score"
        value={formatNumber(dealerImpact.value)}
        note={convexaNote}
        provisional={dealerImpact.provisional}
        daysAccumulated={dealerImpact.days_accumulated}
        accumulatingLabel={accumulatingLabel}
      />
      <MetricCard
        name="Signal Alignment Score"
        value={formatNumber(signalAlignment.value)}
        note={convexaNote}
        provisional={signalAlignment.provisional}
        daysAccumulated={signalAlignment.days_accumulated}
        accumulatingLabel={accumulatingLabel}
      />
      <MetricCard
        name="Market Bias"
        value={labeledValue(marketBias.label, marketBias.score)}
        note={convexaNote}
        provisional={marketBias.provisional}
        daysAccumulated={marketBias.days_accumulated}
        accumulatingLabel={accumulatingLabel}
      />
      <MetricCard
        name="Volatility Regime"
        value={labeledValue(volatility.label, volatility.iv_rank, "IV Rank ")}
        note={t.derivedMetricsBar.volatilityWindowNote}
        provisional={volatility.provisional}
        daysAccumulated={volatility.days_accumulated}
        accumulatingLabel={accumulatingLabel}
      />
    </section>
  );
}
