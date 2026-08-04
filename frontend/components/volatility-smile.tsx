"use client";

import { useEffect, useMemo, useState } from "react";
import { getOptionChain } from "@/lib/api";
import type { OptionChainResponse, OptionContract } from "@/lib/types";

type VolatilitySmileProps = {
  symbol: string;
  marketPrice: number;
};

const PLOT = { left: 58, right: 760, top: 20, bottom: 238 };

function nearestAtmStrike(contracts: OptionContract[], marketPrice: number): number | null {
  const strikes = [...new Set(contracts.map((contract) => contract.strike))];
  if (!strikes.length) return null;
  return strikes.reduce((nearest, strike) =>
    Math.abs(strike - marketPrice) < Math.abs(nearest - marketPrice) ? strike : nearest,
  );
}

function scale(value: number, minimum: number, maximum: number, start: number, end: number) {
  if (maximum <= minimum) return (start + end) / 2;
  return start + ((value - minimum) / (maximum - minimum)) * (end - start);
}

function expirationLabel(expiration: string) {
  return new Intl.DateTimeFormat("es-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${expiration}T00:00:00Z`));
}

export function VolatilitySmile({ symbol, marketPrice }: VolatilitySmileProps) {
  const [expirations, setExpirations] = useState<string[]>([]);
  const [selectedExpiration, setSelectedExpiration] = useState("");
  const [chain, setChain] = useState<OptionChainResponse | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    getOptionChain(symbol, undefined, controller.signal)
      .then((response) => {
        const available = [
          ...new Set(response.contracts.map((contract) => contract.expiration)),
        ].sort();
        setExpirations(available);
        setSelectedExpiration(available[0] ?? "");
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "No se pudo cargar la cadena");
        }
      });
    return () => controller.abort();
  }, [symbol]);

  useEffect(() => {
    if (!selectedExpiration) return;
    const controller = new AbortController();
    getOptionChain(symbol, selectedExpiration, controller.signal)
      .then((response) => {
        setChain(response);
        setError("");
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "No se pudo cargar el vencimiento");
        }
      });
    return () => controller.abort();
  }, [selectedExpiration, symbol]);

  const contracts = useMemo(() => chain?.contracts ?? [], [chain]);
  const plot = useMemo(() => {
    const strikes = contracts.map((contract) => contract.strike);
    const impliedVolatilities = contracts.map((contract) => contract.iv);
    return {
      minimumStrike: Math.min(...strikes),
      maximumStrike: Math.max(...strikes),
      minimumIv: Math.min(...impliedVolatilities),
      maximumIv: Math.max(...impliedVolatilities),
      atmStrike: nearestAtmStrike(contracts, marketPrice),
    };
  }, [contracts, marketPrice]);

  const x = (strike: number) =>
    scale(strike, plot.minimumStrike, plot.maximumStrike, PLOT.left, PLOT.right);
  const y = (iv: number) =>
    scale(iv, plot.minimumIv, plot.maximumIv, PLOT.bottom, PLOT.top);

  return (
    <section className="panel smile-panel" aria-labelledby="smile-title">
      <div className="panel-heading smile-heading">
        <div>
          <p className="eyebrow">Options Chain · IV cruda por strike</p>
          <h2 id="smile-title">Volatility Smile</h2>
          <p className="smile-role">Sentimiento de riesgo en prima · no es un nivel de gravitación</p>
        </div>
        <label className="expiration-control">
          <span>Vencimiento</span>
          <select
            value={selectedExpiration}
            onChange={(event) => setSelectedExpiration(event.target.value)}
            disabled={!expirations.length}
          >
            {expirations.map((expiration) => (
              <option key={expiration} value={expiration}>
                {expirationLabel(expiration)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error ? (
        <p className="smile-status error" role="alert">{error}</p>
      ) : contracts.length ? (
        <div className="smile-chart-wrap">
          <svg
            className="smile-chart"
            viewBox="0 0 800 280"
            role="img"
            aria-label={`Volatility Smile de ${symbol} para ${selectedExpiration}`}
          >
            <line className="smile-axis" x1={PLOT.left} y1={PLOT.bottom} x2={PLOT.right} y2={PLOT.bottom} />
            <line className="smile-axis" x1={PLOT.left} y1={PLOT.top} x2={PLOT.left} y2={PLOT.bottom} />
            {plot.atmStrike !== null && (
              <g aria-label={`Strike ATM ${plot.atmStrike}`}>
                <line className="smile-atm" x1={x(plot.atmStrike)} y1={PLOT.top} x2={x(plot.atmStrike)} y2={PLOT.bottom} />
                <text className="smile-atm-label" x={x(plot.atmStrike)} y={PLOT.top + 12}>ATM {plot.atmStrike}</text>
              </g>
            )}
            {contracts.map((contract) => (
              <circle
                key={contract.occ_symbol}
                className={`smile-point ${contract.type}`}
                cx={x(contract.strike)}
                cy={y(contract.iv)}
                r="5"
                aria-label={`${contract.type} strike ${contract.strike}, IV ${(contract.iv * 100).toFixed(2)}%`}
              />
            ))}
            <text className="smile-axis-label" x={(PLOT.left + PLOT.right) / 2} y="274">Strike</text>
            <text className="smile-axis-label" x="14" y={(PLOT.top + PLOT.bottom) / 2} transform={`rotate(-90 14 ${(PLOT.top + PLOT.bottom) / 2})`}>IV</text>
          </svg>
          <div className="smile-legend" aria-label="Leyenda">
            <span><i className="legend-dot call" /> Calls</span>
            <span><i className="legend-dot put" /> Puts</span>
          </div>
        </div>
      ) : (
        <p className="smile-status">Cargando vencimientos y volatilidad implícita…</p>
      )}
    </section>
  );
}
