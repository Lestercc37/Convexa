"use client";

import { useEffect, useState } from "react";
import { getFutureLive } from "@/lib/api";
import { useLanguage } from "@/lib/i18n/language-context";
import type { FutureLiveResponse } from "@/lib/types";

const POLL_MS = 2_000;
const LIVE_FUTURES = new Set(["ES", "NQ"]);
const points = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signedPoints = (value: number) => `${value >= 0 ? "+" : "−"}${points.format(Math.abs(value))}`;

// ES / NQ pill next to the VIX one (2026-11-06, phase 2 of docs/diseno-es-nq-en-vivo-ninjatrader-2026-11-06.txt): the future - index difference
// Convexa is using right now and where it comes from (NinjaTrader's live bars or the 09:31 anchor), and for the owner only the real last price
// from NinjaTrader (the API leaves `quote` out for everyone else: CME data is for his personal use). Hidden while there is neither.
export function FuturesLiveBadge({ symbol }: { symbol: string }) {
  const { t } = useLanguage();
  const future = symbol.toUpperCase();
  const wanted = LIVE_FUTURES.has(future);
  const [live, setLive] = useState<FutureLiveResponse | null>(null);

  useEffect(() => {
    if (!wanted) return;
    const controller = new AbortController();
    const load = () =>
      getFutureLive(future, controller.signal)
        .then(setLive)
        .catch(() => undefined);
    void load();
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      setLive(null);
    };
  }, [wanted, future]);

  if (!wanted || !live || (live.offset === null && !live.quote)) return null;
  const basis = live.offset !== null ? signedPoints(live.offset) : null;
  const source = live.offset_source === "ninjatrader" ? t.futuresLiveBadge.sourceLive : t.futuresLiveBadge.sourceAnchor;
  const title = t.futuresLiveBadge.title(
    future,
    live.live_basis !== null ? signedPoints(live.live_basis) : "—",
    live.anchor_offset !== null ? signedPoints(live.anchor_offset) : "—",
  );
  return (
    <span className={`mode-pill futures-live-badge${live.offset_source === "ninjatrader" ? " is-live" : ""}`} title={title} aria-label={title}>
      {live.quote && <span className="futures-live-price">{t.futuresLiveBadge.real(future, points.format(live.quote.last))}</span>}
      {basis && t.futuresLiveBadge.basis(basis, source)}
    </span>
  );
}
