"use client";

import { useEffect, useState } from "react";
import { getScreenerPreset } from "@/lib/api";
import type {
  ScreenerPresetName,
  ScreenerPresetResult,
} from "@/lib/types";

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
}: {
  preset: ScreenerPresetName;
  results: ScreenerPresetResult[];
}) {
  if (!results.length) {
    return <p className="screener-empty">No hay resultados persistidos para este preset.</p>;
  }

  return (
    <div className="screener-table-wrap">
      <table className="screener-table">
        <thead>
          {preset === "unusual-options-activity" ? (
            <tr><th>Símbolo</th><th>Contrato</th><th>Tipo</th><th>Monto</th><th>Hora</th></tr>
          ) : preset === "negative-gamma-board" ? (
            <tr><th>Símbolo</th><th>Net Gamma</th><th>Actualizado</th></tr>
          ) : preset === "max-pain-key-levels" ? (
            <tr><th>Símbolo</th><th>Gamma Flip</th><th>Call Wall</th><th>Put Wall</th><th>Max Pain</th></tr>
          ) : (
            <tr><th>Símbolo</th><th>Exposición</th><th>Actualizado</th></tr>
          )}
        </thead>
        <tbody>
          {results.map((item) => (
            <tr key={`${item.symbol}-${item.contract ?? item.as_of}`}>
              <td className="screener-symbol">{item.symbol}</td>
              {preset === "unusual-options-activity" ? (
                <>
                  <td>{item.contract}</td><td>{item.alert_type}</td>
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
  const [preset, setPreset] = useState<ScreenerPresetName>(PRESETS[0].name);
  const [results, setResults] = useState<ScreenerPresetResult[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    getScreenerPreset(preset, controller.signal)
      .then((response) => {
        setResults(response.results);
        setError("");
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "No se pudo cargar el escáner");
        }
      })
    return () => controller.abort();
  }, [preset]);

  return (
    <section className="panel screener-panel" aria-labelledby="quick-screener-title">
      <div className="panel-heading screener-heading">
        <div>
          <p className="eyebrow">Presets Convexa</p>
          <h2 id="quick-screener-title">Escáner Rápido</h2>
        </div>
        <label className="preset-control">
          <span>Preset</span>
          <select value={preset} onChange={(event) => {
            setResults(null);
            setError("");
            setPreset(event.target.value as ScreenerPresetName);
          }}>
            {PRESETS.map((item) => <option key={item.name} value={item.name}>{item.icon} {item.label}</option>)}
          </select>
        </label>
      </div>
      {error ? <p className="screener-empty error" role="alert">{error}</p> : results === null ? (
        <p className="screener-empty" aria-live="polite">Cargando preset…</p>
      ) : <ResultsTable preset={preset} results={results} />}
    </section>
  );
}
