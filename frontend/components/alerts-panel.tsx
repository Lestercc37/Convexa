"use client";

import { useEffect, useState } from "react";
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
  // "vertical" (the sidebar) only changes layout (a column vs. a row) --
  // both show the same single, unified feed (see AlertCard/dominantSide
  // below for why Calls/Puts are no longer split into separate tabs).
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
// volatility-smile.tsx. Singular -- this labels one card's own contract,
// not a tab (the Calls/Puts tabs this used to feed are gone; see the
// unified single feed below).
const SIDE_LABEL: Record<ContractSide, string> = {
  call: "Call",
  put: "Put",
};

const CURRENCY_FORMAT = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const PERCENT_FORMAT = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

// contract, as_of (`timestamp`), and type -- same collision confirmed
// live today in quick-screener.tsx's equivalent key: a single reading
// can independently trip a magnitude threshold (WHALE/UNUSUAL) *and*
// the separate sustained-flow window, so the same symbol+contract+
// timestamp can legitimately carry two distinct alerts with a different
// `type`. Without `type` here, React saw a duplicate key on this panel's
// list (`Encountered two children with the same key`); the identical
// key shape in chart-secondary-panel.tsx's own alertKey() was worse --
// used as a Map key there, so the second alert silently overwrote the
// first instead of just warning.
function alertKey(alert: WhaleAlert) {
  return `${alert.symbol}-${alert.contract}-${alert.timestamp}-${alert.type}`;
}

// Neutral 50/50 fallback when both estimates are zero (e.g. the very
// first finalized minute for a contract) — same "documented midpoint for
// a degenerate case" convention already used for BVC's own σ=0 case.
function buyPercent(alert: WhaleAlert): number {
  const total = alert.estimated_buy_volume + alert.estimated_sell_volume;
  if (total <= 0) return 50;
  return (alert.estimated_buy_volume / total) * 100;
}

type DominantSide = "buy" | "sell" | "mixed";

// "Mixed" isn't an arbitrary near-50 band (that would suppress a real,
// if modest, majority like 52/48) -- it's specifically the exact-50%
// case, which in this codebase only ever comes from a documented "we
// don't have enough signal" fallback: buyPercent's own total<=0 guard
// above, or calculate_bvc_split's identical σ=0 convention on the
// backend (backend/domain/use_cases/calculate_bvc.py). Both construct
// the buy/sell halves as an exact split (half = premium / 2 on the
// backend), so this lands on exactly 50 in floating point too, not
// merely close to it -- confirmed before picking this rule rather than
// assuming a threshold.
function dominantSide(buyPct: number): DominantSide {
  if (buyPct === 50) return "mixed";
  return buyPct > 50 ? "buy" : "sell";
}

function AlertCard({ alert, t }: { alert: WhaleAlert; t: Translations }) {
  const buyPct = buyPercent(alert);
  const sellPct = 100 - buyPct;
  const side = parseContractSide(alert.contract);
  const dominant = dominantSide(buyPct);
  const dominantLabel = {
    buy: t.alertsPanel.dominantBuy,
    sell: t.alertsPanel.dominantSell,
    mixed: t.alertsPanel.dominantMixed,
  }[dominant];
  return (
    <article className={`alert-card alert-${alert.type.toLowerCase()}`}>
      <span className="alert-symbol">{alert.symbol}</span>
      {side && <span className={`alert-side alert-side-${side}`}>{SIDE_LABEL[side]}</span>}
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
          {" · "}
          <span className={`alert-dominant alert-dominant-${dominant}`}>{dominantLabel}</span>
        </span>
      </span>
    </article>
  );
}

export function AlertsPanel({ symbol, orientation = "horizontal" }: AlertsPanelProps) {
  const { t } = useLanguage();
  const [alerts, setAlerts] = useState<WhaleAlert[]>([]);
  const [error, setError] = useState<unknown>(null);
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

  const isVertical = orientation === "vertical";

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
      {error ? (
        <p className="alerts-empty error" role="alert">
          {describeError(error, t)}
        </p>
      ) : alerts.length === 0 ? (
        <p className="alerts-empty" aria-live="polite">
          {t.alertsPanel.empty}
        </p>
      ) : (
        <div
          className={isVertical ? "alerts-column" : "alerts-row"}
          aria-label={t.alertsPanel.recentAriaLabel}
        >
          {alerts.map((alert) => (
            <AlertCard key={alertKey(alert)} alert={alert} t={t} />
          ))}
        </div>
      )}
    </section>
  );
}
