import { useState, type ReactNode } from "react";
import { useLanguage } from "@/lib/i18n/language-context";
import type { DerivedMetrics } from "@/lib/types";

type DerivedMetricsBarProps = {
  metrics: DerivedMetrics;
  // True when the active gamma view is Tactical -- these metrics depend
  // on historical comparisons (DailyGammaReference) that only exist for
  // the Structural window, so they stay Structural regardless of the
  // toggle. A visible badge here is the honest way to show that,
  // instead of silently looking unchanged next to every other panel
  // that DOES follow the toggle.
  showStructuralOnlyBadge?: boolean;
  /** Extra detail sections shown under the cards while the panel is open (DealersPanel only). */
  children?: ReactNode;
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

export function DerivedMetricsBar({ metrics, showStructuralOnlyBadge }: DerivedMetricsBarProps) {
  const { t } = useLanguage();
  const dealerImpact = metrics.dealer_impact_score;
  const signalAlignment = metrics.signal_alignment_score;
  const marketBias = metrics.market_bias;
  const volatility = metrics.volatility_regime;
  const convexaNote = t.common.convexaNote;
  const accumulatingLabel = t.derivedMetricsBar.accumulating;

  return (
    <section className="panel metrics-bar" aria-label={t.derivedMetricsBar.ariaLabel}>
      {showStructuralOnlyBadge && (
        <span className="metrics-bar-structural-badge">{t.dashboard.structuralOnlyBadge}</span>
      )}
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

type DealersPanelState = "folded" | "open" | "hidden";

/**
 * The Dealers group (Dealer Impact / Signal Alignment / Market Bias / Volatility Regime) as the approved design 12.7 asks: folded by default,
 * each tab shows its own number, one click opens the detail cards, "Ocultar panel" hides the whole group (a small button brings it back).
 * The state lives in the page only (a reload starts folded again).
 */
export function DealersPanel({ metrics, showStructuralOnlyBadge, children }: DerivedMetricsBarProps) {
  const { t } = useLanguage();
  const [state, setState] = useState<DealersPanelState>("folded");
  if (state === "hidden") {
    return (
      <button type="button" className="dealers-show" onClick={() => setState("folded")}>
        {t.derivedMetricsBar.showPanel}
      </button>
    );
  }
  const open = state === "open";
  const tabs = [
    { key: "dealer", name: t.derivedMetricsBar.tabDealer, value: formatNumber(metrics.dealer_impact_score.value) },
    { key: "signal", name: t.derivedMetricsBar.tabSignal, value: formatNumber(metrics.signal_alignment_score.value) },
    { key: "bias", name: t.derivedMetricsBar.tabBias, value: labeledValue(metrics.market_bias.label, metrics.market_bias.score) },
    {
      key: "volatility",
      name: t.derivedMetricsBar.tabVolatility,
      value: labeledValue(metrics.volatility_regime.label, metrics.volatility_regime.iv_rank, "IV Rank "),
    },
  ];
  return (
    <div className="dealers-panel">
      <div className="dealers-tabs" role="group" aria-label={t.derivedMetricsBar.tabsAriaLabel}>
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            className="dealers-tab"
            aria-expanded={open}
            onClick={() => setState(open ? "folded" : "open")}
          >
            <span className="dealers-tab-name">{tab.name}</span>
            <strong className="dealers-tab-value">{tab.value}</strong>
          </button>
        ))}
        <button type="button" className="dealers-hide" onClick={() => setState("hidden")}>
          {t.derivedMetricsBar.hidePanel}
        </button>
      </div>
      {open && (
        <div className="dealers-detail">
          <DerivedMetricsBar metrics={metrics} showStructuralOnlyBadge={showStructuralOnlyBadge} />
          {children}
        </div>
      )}
    </div>
  );
}
