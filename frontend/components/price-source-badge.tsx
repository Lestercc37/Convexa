"use client";

import { useEffect, useState } from "react";
import { getPriceSources } from "@/lib/api";
import { useLanguage } from "@/lib/i18n/language-context";
import type { PriceSource } from "@/lib/types";

const POLL_MS = 15_000;
// The index each chart's price comes from (ES / NQ are SPX / NDX + the anchor).
const INDEX_FOR_SYMBOL: Record<string, string> = { SPX: "SPX", ES: "SPX", NDX: "NDX", NQ: "NDX" };
const etClock = new Intl.DateTimeFormat("es-ES", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false });

/** The index the price of `symbol` follows (SPX for ES), null for a symbol with a price of its own. */
export function priceIndexFor(symbol: string): string | null {
  return INDEX_FOR_SYMBOL[symbol.toUpperCase()] ?? null;
}

// Amber pill in the top bar while the index of this chart is written from its ETF because ThetaData's index feed lags (backend
// use_cases/index_stale_guard.py, 2026-11-06: every index 8-10 minutes late). Nothing is shown the rest of the time. A failed read keeps what is shown.
export function PriceSourceBadge({ symbol }: { symbol: string }) {
  const { t } = useLanguage();
  const index = priceIndexFor(symbol);
  const [replaced, setReplaced] = useState<PriceSource[]>([]);

  useEffect(() => {
    if (!index) return;
    const controller = new AbortController();
    const load = () =>
      getPriceSources(controller.signal)
        .then((response) => setReplaced(response.replaced))
        .catch(() => undefined);
    void load();
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [index]);

  const source = index ? replaced.find((item) => item.symbol === index) : undefined;
  if (!source) return null;
  const since = etClock.format(new Date(source.since));
  const title = t.priceSourceBadge.title(source.symbol, source.proxy, source.ratio.toFixed(4), since);
  return (
    <span className="mode-pill price-source-badge" role="status" title={title} aria-label={title}>
      {t.priceSourceBadge.label(source.symbol, source.proxy)}
    </span>
  );
}
