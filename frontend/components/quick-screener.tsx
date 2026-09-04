"use client";

import { useEffect, useState } from "react";
import { getScreenerPreset } from "@/lib/api";
import { describeError } from "@/lib/i18n/describe-error";
import { useLanguage } from "@/lib/i18n/language-context";
import type { Translations } from "@/lib/i18n/translations";
import { POLLING_INTERVAL_MS } from "@/lib/polling";
import type {
  ScreenerPresetName,
  ScreenerPresetResult,
} from "@/lib/types";
import { TYPE_LABEL } from "./alerts-panel";
import { ScreenerPresetSettingsPanel } from "./screener-preset-settings-panel";

const PRESETS: { name: ScreenerPresetName; label: string; icon: string }[] = [
  { name: "unusual-options-activity", label: "Unusual Options Activity", icon: "🔥" },
  { name: "negative-gamma-board", label: "Negative Gamma Board", icon: "⚡" },
  { name: "max-pain-key-levels", label: "Max Pain & Key Levels", icon: "🧲" },
  { name: "vanna-exposure-leaders", label: "Vanna Exposure Leaders", icon: "🌊" },
  { name: "charm-decay-pressure", label: "Charm Decay Pressure", icon: "⏳" },
];

const NUMBER_FORMAT = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const CURRENCY_FORMAT = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

function number(value: number | null) {
  return value === null ? "—" : NUMBER_FORMAT.format(value);
}

function currency(value: number | null) {
  return value === null ? "—" : CURRENCY_FORMAT.format(value);
}

function ResultsTable({
  preset,
  results,
  t,
}: {
  preset: ScreenerPresetName;
  results: ScreenerPresetResult[];
  t: Translations;
}) {
  if (!results.length) {
    return <p className="screener-empty">{t.quickScreener.noResults}</p>;
  }
  const headers = t.quickScreener.headers;

  return (
    <div className="screener-table-wrap">
      <table className="screener-table">
        <thead>
          {preset === "unusual-options-activity" ? (
            <tr><th>{headers.symbol}</th><th>{headers.contract}</th><th>{headers.type}</th><th>{headers.amount}</th><th>{headers.time}</th></tr>
          ) : preset === "negative-gamma-board" ? (
            <tr><th>{headers.symbol}</th><th>Net Gamma</th><th>{headers.updated}</th></tr>
          ) : preset === "max-pain-key-levels" ? (
            <tr><th>{headers.symbol}</th><th>Gamma Flip</th><th>Call Wall</th><th>Put Wall</th><th>Max Pain</th></tr>
          ) : (
            <tr><th>{headers.symbol}</th><th>{headers.exposure}</th><th>{headers.updated}</th></tr>
          )}
        </thead>
        <tbody>
          {results.map((item) => (
            // contract *and* as_of *and* alert_type -- confirmed live
            // against the real running backend, 2026-09, in two distinct
            // ways this needs all three:
            // 1. The same contract legitimately racks up multiple UNUSUAL
            //    alerts across the session as volume keeps flowing (e.g.
            //    QQQ260904C00721000 more than once, each a real, separate
            //    WhaleAlert with its own as_of) -- dropping `as_of`
            //    whenever `contract` was present (the original `??`
            //    fallback) collided those together.
            // 2. A single reading can independently trip a magnitude
            //    threshold (WHALE/UNUSUAL) *and* the separate sustained-
            //    flow window, so the same symbol+contract+as_of can also
            //    appear twice with a different alert_type (e.g.
            //    QQQ260904C00722000 as both SUSTAINED_FLOW and UNUSUAL,
            //    same microsecond as_of).
            // None of these are duplicates to dedupe away -- every row is
            // a real, distinct alert. Same WHALE+SUSTAINED_FLOW collision
            // shape as alertKey() in alerts-panel.tsx/chart-secondary-panel.tsx,
            // deliberately not touched here.
            <tr key={`${item.symbol}-${item.contract ?? "agg"}-${item.as_of}-${item.alert_type ?? ""}`}>
              <td className="screener-symbol">{item.symbol}</td>
              {preset === "unusual-options-activity" ? (
                <>
                  <td>{item.contract}</td><td>{item.alert_type ? TYPE_LABEL[item.alert_type] : "—"}</td>
                  <td>{currency(item.amount)}</td><td>{new Date(item.as_of).toLocaleTimeString()}</td>
                </>
              ) : preset === "negative-gamma-board" ? (
                <><td>{number(item.net_gamma)}</td><td>{new Date(item.as_of).toLocaleTimeString()}</td></>
              ) : preset === "max-pain-key-levels" ? (
                <>
                  <td>{number(item.gamma_flip)}</td><td>{number(item.call_wall)}</td>
                  <td>{number(item.put_wall)}</td><td>{number(item.max_pain)}</td>
                </>
              ) : (
                <>
                  <td>{number(preset === "vanna-exposure-leaders" ? item.vanna_exposure : item.charm_exposure)}</td>
                  <td>{new Date(item.as_of).toLocaleTimeString()}</td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function QuickScreener() {
  const { t } = useLanguage();
  const [preset, setPreset] = useState<ScreenerPresetName>(PRESETS[0].name);
  const [results, setResults] = useState<ScreenerPresetResult[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [showSettingsPanel, setShowSettingsPanel] = useState(false);

  useEffect(() => {
    const controller = new AbortController();

    const refresh = async () => {
      try {
        const response = await getScreenerPreset(preset, controller.signal);
        setResults(response.results);
        setError(null);
      } catch (reason: unknown) {
        if (!controller.signal.aborted) {
          setError(reason);
        }
      }
    };

    void refresh();
    // Same 30s cadence as the rest of the dashboard (POLLING_INTERVAL_MS)
    // -- confirmed live this preset is cheap (reads already-computed
    // aggregates, no recalculation; ~250ms end to end against the real
    // backend across all active symbols), so this interval doesn't need
    // to be longer or configurable.
    const interval = window.setInterval(() => void refresh(), POLLING_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [preset]);

  return (
    <section className="panel screener-panel" aria-labelledby="quick-screener-title">
      <div className="panel-heading screener-heading">
        <div>
          <p className="eyebrow">{t.quickScreener.eyebrow}</p>
          <h2 id="quick-screener-title">{t.quickScreener.title}</h2>
        </div>
        <div className="screener-preset-row">
          <label className="preset-control">
            <span>{t.quickScreener.presetLabel}</span>
            <select value={preset} onChange={(event) => {
              setResults(null);
              setError(null);
              setPreset(event.target.value as ScreenerPresetName);
            }}>
              {PRESETS.map((item) => <option key={item.name} value={item.name}>{item.icon} {item.label}</option>)}
            </select>
          </label>
          <button
            type="button"
            className="tv-settings-button"
            aria-label={t.screenerPresetSettingsPanel.triggerAriaLabel}
            onClick={() => setShowSettingsPanel(true)}
          >
            ⚙
          </button>
        </div>
      </div>
      {showSettingsPanel && (
        <ScreenerPresetSettingsPanel onClose={() => setShowSettingsPanel(false)} />
      )}
      {error ? <p className="screener-empty error" role="alert">{describeError(error, t)}</p> : results === null ? (
        <p className="screener-empty" aria-live="polite">{t.quickScreener.loading}</p>
      ) : <ResultsTable preset={preset} results={results} t={t} />}
    </section>
  );
}
