"use client";

import { useEffect, useState } from "react";
import { getAlerts } from "@/lib/api";
import { describeError } from "@/lib/i18n/describe-error";
import { useLanguage } from "@/lib/i18n/language-context";
import { POLLING_INTERVAL_MS } from "@/lib/polling";
import type { Underlying, WhaleAlert } from "@/lib/types";

type AlertsPanelProps = {
  underlyings: Underlying[];
  // "horizontal" (default) is the original scrollable strip — kept as an
  // option in case this panel is ever reused outside the left sidebar.
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

const CURRENCY_FORMAT = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

function alertKey(alert: WhaleAlert) {
  return `${alert.symbol}-${alert.contract}-${alert.timestamp}`;
}

export function AlertsPanel({ underlyings, orientation = "horizontal" }: AlertsPanelProps) {
  const { t } = useLanguage();
  const [alerts, setAlerts] = useState<WhaleAlert[]>([]);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (!underlyings.length) return;
    const controller = new AbortController();

    const refresh = async () => {
      try {
        const responses = await Promise.all(
          underlyings.map((underlying) => getAlerts(underlying.symbol, controller.signal)),
        );
        const combined = responses
          .flatMap((response) => response.alerts)
          .sort((left, right) => Date.parse(right.timestamp) - Date.parse(left.timestamp));
        setAlerts(combined);
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
  }, [underlyings]);

  return (
    <section
      className={`panel alerts-panel alerts-panel-${orientation}`}
      aria-labelledby="alerts-panel-title"
    >
      <div className="panel-heading alerts-heading">
        <p className="eyebrow">{t.alertsPanel.eyebrow}</p>
        <h2 id="alerts-panel-title">{t.alertsPanel.title}</h2>
      </div>
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
          className={orientation === "vertical" ? "alerts-column" : "alerts-row"}
          aria-label={t.alertsPanel.recentAriaLabel}
        >
          {alerts.map((alert) => (
            <article key={alertKey(alert)} className={`alert-card alert-${alert.type.toLowerCase()}`}>
              <span className="alert-symbol">{alert.symbol}</span>
              <span className="alert-contract">{alert.contract}</span>
              <span className="alert-type">{TYPE_LABEL[alert.type]}</span>
              <span className="alert-amount">{CURRENCY_FORMAT.format(alert.amount)}</span>
              <span className="alert-time">{new Date(alert.timestamp).toLocaleTimeString()}</span>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
