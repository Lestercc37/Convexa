"use client";

import { useEffect, useState } from "react";
import { DerivedMetricsBar } from "@/components/derived-metrics-bar";
import { PanelWindow } from "@/components/v2-panel-window";
import { getGamma } from "@/lib/api";
import { DEFAULT_GAMMA_VIEW, GAMMA_SOURCE_SYMBOL_BY_FUTURE } from "@/lib/gamma-views";
import { POLLING_INTERVAL_MS } from "@/lib/polling";
import type { DerivedMetrics } from "@/lib/types";

// The four Dealers cards (dealer impact, signal alignment, market bias, volatility regime) in their own desktop-app window: /vista/dealers?simbolo=SPX
function DealersCards({ symbol }: { symbol: string }) {
  const [metrics, setMetrics] = useState<DerivedMetrics | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const source = GAMMA_SOURCE_SYMBOL_BY_FUTURE[symbol] ?? symbol;
    const load = () =>
      getGamma(source, DEFAULT_GAMMA_VIEW, controller.signal)
        .then((gamma) => {
          setMetrics(gamma.derived_metrics);
          setFailed(false);
        })
        .catch(() => {
          if (!controller.signal.aborted) setFailed(true);
        });
    void load();
    const interval = window.setInterval(() => void load(), POLLING_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [symbol]);

  if (!metrics) return <p className="v2-panel-note">{failed ? "No se pudieron leer los datos; reintentando." : "Cargando…"}</p>;
  return <DerivedMetricsBar metrics={metrics} />;
}

export default function DealersWindowPage() {
  return <PanelWindow title="Dealers">{(symbol) => <DealersCards key={symbol} symbol={symbol} />}</PanelWindow>;
}
