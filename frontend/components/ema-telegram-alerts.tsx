"use client";

import { useCallback, useEffect, useState } from "react";

import { addEmaAlert, deleteEmaAlert, getEmaAlerts, newTelegramLinkCode, type EmaAlertsState, type TelegramLinkCode } from "@/lib/api";
import { useLanguage } from "@/lib/i18n/language-context";

// EMA cross alerts by Telegram, inside the EMAs menu (Lester 2026-10-25 / 11-06): "Avisar por Telegram" for one EMA pair (8/20 by default, both directions) on the
// chart's symbol and timeframe, "Conectar Telegram" with a one-time code for the bot, and the user's alerts on every symbol with a remove button. The server evaluates the
// rules on closed candles (backend/ema_alerts_worker.py), so they work with Convexa closed. Rules are per user, apart from the chart template.
const ALERT_TIMEFRAMES = new Set(["1m", "5m", "15m", "1h"]);
const LINK_POLL_MS = 5_000;

type Props = { symbol: string; timeframe: string };

export function EmaTelegramAlerts({ symbol, timeframe }: Props) {
  const { t } = useLanguage();
  const labels = t.priceChart;
  const [state, setState] = useState<EmaAlertsState | null>(null);
  const [fast, setFast] = useState(8);
  const [slow, setSlow] = useState(20);
  const [code, setCode] = useState<TelegramLinkCode | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const reload = useCallback(async () => {
    try {
      setState(await getEmaAlerts());
    } catch {
      // not logged in or offline: the section stays empty
    }
  }, []);

  useEffect(() => {
    let alive = true;
    getEmaAlerts()
      .then((loaded) => {
        if (alive) setState(loaded);
      })
      .catch(() => undefined); // not logged in or offline: the section stays empty
    return () => {
      alive = false;
    };
  }, []);

  // While a code is shown and the chat is not linked yet, look every few seconds so "conectado" appears as soon as the bot got it.
  const waiting = code !== null && state !== null && !state.telegram_linked;
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setInterval(() => void reload(), LINK_POLL_MS);
    return () => window.clearInterval(timer);
  }, [waiting, reload]);

  const upper = symbol.toUpperCase();
  const supported = ALERT_TIMEFRAMES.has(timeframe) && /^[A-Z]{1,6}$/.test(upper);
  const validPair = Number.isInteger(fast) && Number.isInteger(slow) && fast >= 2 && slow <= 400 && fast < slow;
  const current = state?.rules.find((rule) => rule.symbol === upper && rule.timeframe === timeframe && rule.fast_period === fast && rule.slow_period === slow) ?? null;

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setFailed(false);
    try {
      await action();
      await reload();
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  const toggle = (on: boolean) =>
    run(async () => {
      if (on) await addEmaAlert({ symbol: upper, timeframe, fast_period: fast, slow_period: slow });
      else if (current) await deleteEmaAlert(current.id);
    });

  const connect = () =>
    run(async () => {
      setCode(await newTelegramLinkCode());
    });

  return (
    <section className="ema-alerts" aria-label={labels.emaAlertsTitle}>
      <p className="ema-alerts-title">{labels.emaAlertsTitle}</p>
      {state?.telegram_linked ? (
        <p className="ema-alerts-linked">{labels.emaAlertsLinked}</p>
      ) : (
        <button type="button" className="ema-alerts-connect" disabled={busy || state === null} onClick={() => void connect()}>
          {labels.emaAlertsConnect}
        </button>
      )}
      {code && !state?.telegram_linked && (
        <p className="ema-alerts-code">
          {labels.emaAlertsCode(code.code, code.bot_username)}{" "}
          <a href={code.link} target="_blank" rel="noreferrer">
            {labels.emaAlertsOpenBot(code.bot_username)}
          </a>
        </p>
      )}
      {supported ? (
        <div className="ema-alerts-pair">
          <input
            type="number"
            min={2}
            max={399}
            value={fast}
            aria-label={labels.emaAlertsFast}
            onChange={(event) => setFast(Math.round(Number(event.target.value)))}
          />
          <span>/</span>
          <input
            type="number"
            min={3}
            max={400}
            value={slow}
            aria-label={labels.emaAlertsSlow}
            onChange={(event) => setSlow(Math.round(Number(event.target.value)))}
          />
          <label>
            <input
              type="checkbox"
              checked={current !== null}
              disabled={busy || state === null || !validPair}
              onChange={(event) => void toggle(event.target.checked)}
            />
            {labels.emaAlertsToggle(upper, timeframe)}
          </label>
        </div>
      ) : (
        <p className="ema-alerts-note">{labels.emaAlertsUnsupported}</p>
      )}
      {failed && (
        <p className="ema-menu-error" role="alert">
          {labels.emaAlertsError}
        </p>
      )}
      {state && state.rules.length > 0 && (
        <ul className="ema-alerts-list">
          {state.rules.map((rule) => {
            const label = labels.emaAlertsRule(rule.symbol, rule.timeframe, rule.fast_period, rule.slow_period);
            return (
              <li key={rule.id}>
                <span>{label}</span>
                <button type="button" className="ema-menu-remove" aria-label={labels.emaAlertsRemove(label)} disabled={busy} onClick={() => void run(() => deleteEmaAlert(rule.id))}>
                  ×
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
