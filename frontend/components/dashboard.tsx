"use client";

import { useCallback, useEffect, useState } from "react";
import { getGamma, getMarket, getUnderlyings } from "@/lib/api";
import type { GammaResponse, MarketResponse, Underlying } from "@/lib/types";
import { GravityMap } from "./gravity-map";
import { RegimeBadge } from "./regime-badge";

const POLLING_INTERVAL_MS = 30_000;

export function Dashboard() {
  const [underlyings, setUnderlyings] = useState<Underlying[]>([]);
  const [symbol, setSymbol] = useState("");
  const [gamma, setGamma] = useState<GammaResponse | null>(null);
  const [market, setMarket] = useState<MarketResponse | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    getUnderlyings(controller.signal)
      .then(({ underlyings: items }) => {
        setUnderlyings(items);
        setSymbol((current) => current || items[0]?.symbol || "");
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "No se pudo cargar la lista");
        }
      });
    return () => controller.abort();
  }, []);

  const refresh = useCallback(async (activeSymbol: string, signal?: AbortSignal) => {
    if (!activeSymbol) return;
    try {
      const [gammaData, marketData] = await Promise.all([
        getGamma(activeSymbol, signal),
        getMarket(activeSymbol, signal),
      ]);
      setGamma(gammaData);
      setMarket(marketData);
      setError("");
    } catch (reason: unknown) {
      if (!signal?.aborted) {
        setError(reason instanceof Error ? reason.message : "No se pudieron cargar los datos");
      }
    }
  }, []);

  useEffect(() => {
    if (!symbol) return;
    const controller = new AbortController();
    const initialRefresh = window.setTimeout(
      () => void refresh(symbol, controller.signal),
      0,
    );
    const interval = window.setInterval(() => void refresh(symbol), POLLING_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearTimeout(initialRefresh);
      window.clearInterval(interval);
    };
  }, [refresh, symbol]);

  return (
    <main className="dashboard">
      <header className="topbar">
        <div>
          <p className="eyebrow">Convexa · Options Intelligence</p>
          <h1>Gamma Dashboard</h1>
        </div>
        <div className="symbol-control">
          <label htmlFor="symbol">Underlying</label>
          <select
            id="symbol"
            value={symbol}
            onChange={(event) => {
              setGamma(null);
              setMarket(null);
              setSymbol(event.target.value);
            }}
            disabled={!underlyings.length}
          >
            {underlyings.map((item) => (
              <option key={item.symbol} value={item.symbol}>
                {item.symbol}
              </option>
            ))}
          </select>
        </div>
      </header>

      {error ? (
        <section className="panel status error" role="alert">
          {error}
        </section>
      ) : gamma && market ? (
        <div className="content-grid">
          <RegimeBadge gamma={gamma} market={market} />
          <GravityMap gamma={gamma} market={market} />
        </div>
      ) : (
        <section className="panel status" aria-live="polite">
          Cargando régimen y niveles…
        </section>
      )}
    </main>
  );
}
