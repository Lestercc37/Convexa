"use client";

import { useEffect, useMemo, useState } from "react";
import { getAlerts } from "@/lib/api";
import { describeError } from "@/lib/i18n/describe-error";
import { useLanguage } from "@/lib/i18n/language-context";
import type { Translations } from "@/lib/i18n/translations";
import { type ContractSide, parseContractSide } from "@/lib/occ-symbol";
import { POLLING_INTERVAL_MS } from "@/lib/polling";
import type { WhaleAlert } from "@/lib/types";
import { WhaleThresholdsPanel } from "./whale-thresholds-panel";

type AlertsPanelProps = {
  // Per-symbol by design (confirmed with product before this change) --
  // the active chart symbol, not the whole underlyings universe.
  symbol: string;
  // "horizontal" (default) is the original scrollable strip — kept as an
  // option in case this panel is ever reused outside the left sidebar.
  // The Calls/Puts split below only applies to "vertical" (the sidebar),
  // matching the task's own scope ("dentro de la misma columna izquierda").
  orientation?: "horizontal" | "vertical";
};

// English regardless of UI language — same "Whale"/"Unusual" alert
// vocabulary GEXBot-style tools use; not Spanish prose to translate.
// Exported so quick-screener.tsx renders the same labels instead of the
// raw backend enum ("WHALE"/"UNUSUAL").
export const TYPE_LABEL: Record<WhaleAlert["type"], string> = {
  WHALE: "Whale",
  UNUSUAL: "Unusual",
  SUSTAINED_FLOW: "Sustained Flow",
};

// English regardless of UI language, matching the Calls/Puts legend
// already hardcoded this way in pre-session-panel.tsx and
// volatility-smile.tsx.
const SIDE_LABEL: Record<ContractSide, string> = {
  call: "Calls",
  put: "Puts",
};

const CURRENCY_FORMAT = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const PERCENT_FORMAT = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

function alertKey(alert: WhaleAlert) {
  return `${alert.symbol}-${alert.contract}-${alert.timestamp}`;
}

// Neutral 50/50 fallback when both estimates are zero (e.g. the very
// first finalized minute for a contract) — same "documented midpoint for
// a degenerate case" convention already used for BVC's own σ=0 case.
function buyPercent(alert: WhaleAlert): number {
  const total = alert.estimated_buy_volume + alert.estimated_sell_volume;
  if (total <= 0) return 50;
  return (alert.estimated_buy_volume / total) * 100;
}

function AlertCard({ alert, t }: { alert: WhaleAlert; t: Translations }) {
  const buyPct = buyPercent(alert);
  const sellPct = 100 - buyPct;
  return (
    <article className={`alert-card alert-${alert.type.toLowerCase()}`}>
      <span className="alert-symbol">{alert.symbol}</span>
      <span className="alert-contract">{alert.contract}</span>
      <span className="alert-type">{TYPE_LABEL[alert.type]}</span>
      <span className="alert-amount">{CURRENCY_FORMAT.format(alert.amount)}</span>
      <span className="alert-time">{new Date(alert.timestamp).toLocaleTimeString()}</span>
      <span className="alert-bvc" title={t.alertsPanel.bvcLabel}>
        <span
          className="alert-bvc-bar"
          role="img"
          aria-label={t.alertsPanel.bvcAriaLabel(
            Math.round(buyPct),
            Math.round(sellPct),
          )}
        >
          <span className="alert-bvc-buy" style={{ width: `${buyPct}%` }} />
          <span className="alert-bvc-sell" style={{ width: `${sellPct}%` }} />
        </span>
        <span className="alert-bvc-caption">
          {t.alertsPanel.bvcLabel} · {PERCENT_FORMAT.format(buyPct)}% / {PERCENT_FORMAT.format(sellPct)}%
        </span>
      </span>
    </article>
  );
}

export function AlertsPanel({ symbol, orientation = "horizontal" }: AlertsPanelProps) {
  const { t } = useLanguage();
  const [alerts, setAlerts] = useState<WhaleAlert[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [activeSide, setActiveSide] = useState<ContractSide>("call");
  const [showThresholdsPanel, setShowThresholdsPanel] = useState(false);

  useEffect(() => {
    if (!symbol) return;
    const controller = new AbortController();

    const refresh = async () => {
      try {
        // recent_alerts() (backend/domain/use_cases/flow.py) already
        // returns most-recent-first -- no client-side sort needed for a
        // single symbol's response (that sort only mattered when merging
        // several symbols' responses together, before this was
        // per-symbol).
        const response = await getAlerts(symbol, controller.signal);
        setAlerts(response.alerts);
        setError(null);
      } catch (reason: unknown) {
        if (!controller.signal.aborted) {
          setError(reason);
        }
      }
    };

    void refresh();
    const interval = window.setInterval(() => void refresh(), POLLING_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
    // Re-runs (and its cleanup clears the previous interval) whenever the
    // active chart symbol changes, not just on its own 30s cadence.
  }, [symbol]);

  // Only the sidebar (vertical) splits by side — the horizontal strip is
  // legacy/unused chrome kept as an option, out of this task's scope.
  const isVertical = orientation === "vertical";

  const bySide = useMemo(() => {
    const groups: Record<ContractSide, WhaleAlert[]> = { call: [], put: [] };
    for (const alert of alerts) {
      const side = parseContractSide(alert.contract);
      if (side) groups[side].push(alert);
    }
    return groups;
  }, [alerts]);

  const visibleAlerts = isVertical ? bySide[activeSide] : alerts;

  return (
    <section
      className={`panel alerts-panel alerts-panel-${orientation}`}
      aria-labelledby="alerts-panel-title"
    >
      <div className="panel-heading alerts-heading">
        <div>
          <p className="eyebrow">{t.alertsPanel.eyebrow}</p>
          <h2 id="alerts-panel-title">{t.alertsPanel.title}</h2>
        </div>
        <button
          type="button"
          className="tv-settings-button"
          aria-label={t.dashboard.settingsButtonAriaLabel}
          onClick={() => setShowThresholdsPanel(true)}
        >
          ⚙
        </button>
      </div>
      {showThresholdsPanel && (
        <WhaleThresholdsPanel onClose={() => setShowThresholdsPanel(false)} />
      )}
      {isVertical && !error && (
        <div
          className="alerts-side-tabs"
          role="group"
          aria-label={t.alertsPanel.sideTabsAriaLabel}
        >
          {(["call", "put"] as ContractSide[]).map((side) => (
            <button
              key={side}
              type="button"
              aria-pressed={activeSide === side}
              onClick={() => setActiveSide(side)}
            >
              {SIDE_LABEL[side]}
            </button>
          ))}
        </div>
      )}
      {error ? (
        <p className="alerts-empty error" role="alert">
          {describeError(error, t)}
        </p>
      ) : visibleAlerts.length === 0 ? (
        <p className="alerts-empty" aria-live="polite">
          {t.alertsPanel.empty}
        </p>
      ) : (
        <div
          className={isVertical ? "alerts-column" : "alerts-row"}
          aria-label={t.alertsPanel.recentAriaLabel}
        >
          {visibleAlerts.map((alert) => (
            <AlertCard key={alertKey(alert)} alert={alert} t={t} />
          ))}
        </div>
      )}
    </section>
  );
}
