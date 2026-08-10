"use client";

import { useEffect, useState } from "react";
import { getAlerts } from "@/lib/api";
import { POLLING_INTERVAL_MS } from "@/lib/polling";
import type { Underlying, WhaleAlert } from "@/lib/types";

type AlertsPanelProps = {
  underlyings: Underlying[];
};

const TYPE_LABEL: Record<WhaleAlert["type"], string> = {
  WHALE: "Whale",
  UNUSUAL: "Unusual",
};

const CURRENCY_FORMAT = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

function alertKey(alert: WhaleAlert) {
  return `${alert.symbol}-${alert.contract}-${alert.timestamp}`;
}

export function AlertsPanel({ underlyings }: AlertsPanelProps) {
  const [alerts, setAlerts] = useState<WhaleAlert[]>([]);
  const [error, setError] = useState("");

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
        setError("");
      } catch (reason: unknown) {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "No se pudieron cargar las alertas");
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
    <section className="panel alerts-panel" aria-labelledby="alerts-panel-title">
      <div className="panel-heading alerts-heading">
        <p className="eyebrow">Whale Alerts</p>
        <h2 id="alerts-panel-title">Alertas</h2>
      </div>
      {error ? (
        <p className="alerts-empty error" role="alert">
          {error}
        </p>
      ) : alerts.length === 0 ? (
        <p className="alerts-empty" aria-live="polite">
          Sin alertas recientes.
        </p>
      ) : (
        <div className="alerts-row" aria-label="Alertas recientes">
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
