"use client";

import { useEffect, useState } from "react";
import { getMarket, getMarketPriceHistory } from "@/lib/api";
import { useLanguage } from "@/lib/i18n/language-context";
import { POLLING_INTERVAL_MS } from "@/lib/polling";
import { translateVixChange, vixBand, vixOpenFrom, vixTargetFor } from "@/lib/vix-translator";

const OPEN_REFRESH_MS = 5 * 60_000;
const twoDecimals = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const whole = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0, signDisplay: "always" });
// The same minus sign as the per-point band ("−16 a −32"), not the hyphen Intl writes.
const signed = (value: number) => whole.format(value).replace("-", "−");

// The VIX -> SPX / NDX pill in the top bar, next to MOV. ESP. (Lester 2026-11-05): the VIX now, how many index points one VIX point has meant
// at this VIX level, and today's VIX change since the open translated with the band of the opening level. Only for SPX / ES and NDX / NQ.
// Reads the VIX itself (/market/VIX every 30 s, its price history for the open every 5 min); a failed read keeps what is shown.
export function VixTranslatorBadge({ symbol }: { symbol: string }) {
  const { t } = useLanguage();
  const target = vixTargetFor(symbol);
  const [vix, setVix] = useState<number | null>(null);
  const [vixOpen, setVixOpen] = useState<number | null>(null);

  useEffect(() => {
    if (!target) return;
    const controller = new AbortController();
    const loadPrice = () =>
      getMarket("VIX", controller.signal)
        .then((market) => setVix(market.price > 0 ? market.price : null))
        .catch(() => undefined);
    const loadOpen = () =>
      getMarketPriceHistory("VIX", controller.signal)
        .then((history) => setVixOpen(vixOpenFrom(history.points)))
        .catch(() => undefined);
    void loadPrice();
    void loadOpen();
    const priceTimer = window.setInterval(() => void loadPrice(), POLLING_INTERVAL_MS);
    const openTimer = window.setInterval(() => void loadOpen(), OPEN_REFRESH_MS);
    return () => {
      controller.abort();
      window.clearInterval(priceTimer);
      window.clearInterval(openTimer);
    };
  }, [target]);

  if (!target || vix === null) return null;
  const band = vixBand(target, vix);
  const day = vixOpen !== null ? translateVixChange(target, vixOpen, vix) : null;
  const change = vixOpen !== null ? vix - vixOpen : null;
  const title = t.vixBadge.title(target, band.low, band.high);
  return (
    <span className="mode-pill vix-translator-compact" title={title} aria-label={title}>
      VIX {twoDecimals.format(vix)} · {t.vixBadge.perPoint(target, band.low, band.high)}
      {day && change !== null && (
        <span className="vix-translator-day">
          {t.vixBadge.today(`${change > 0 ? "+" : "−"}${twoDecimals.format(Math.abs(change))}`, signed(day.from), signed(day.to))}
        </span>
      )}
    </span>
  );
}
