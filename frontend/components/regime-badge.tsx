import { useLanguage } from "@/lib/i18n/language-context";
import type { GammaResponse } from "@/lib/types";

// "+$18.4B" / "-$29.5M" -- signDisplay makes a positive net_gamma show its
// own explicit "+" (Intl otherwise only marks negatives), notation:
// "compact" is what turns a real net_gamma (single- to low-double-digit
// billions for an index, tens of millions for a single stock -- confirmed
// against live gamma_aggregates: SPX ~$16.6B, AAPL ~$46M) into "B"/"M"
// instead of a wall of digits.
const compactCurrency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
  signDisplay: "exceptZero",
});

// Simplified to exactly two fields -- the regime label and the total
// dollar gamma amount -- per explicit request (2026-09-14): everything
// else the badge used to show (price-vs-Gamma-Flip detail, the "unconfirmed
// regime" warning, the update-frequency note) is gone. Deliberately reads
// both fields off `gamma` alone (dealer_position, net_gamma), the same
// pattern RegimeCompactBadge below already uses and its own test
// documents: that's what makes the label and the amount structurally
// unable to disagree, and is why this component no longer needs a
// `market` prop at all -- the "unconfirmed" state it used to show existed
// specifically to flag `market.dealer_mode` disagreeing with price, which
// can't happen anymore now that `market` isn't consulted here.
export function RegimeBadge({ gamma }: { gamma: GammaResponse }) {
  const { t } = useLanguage();
  const isLong = gamma.dealer_position === "long_gamma";

  return (
    <section
      className={`panel regime-badge ${isLong ? "long" : "short"}`}
      aria-label={t.regimeBadge.ariaLabel}
    >
      <h2 className="regime-label">{isLong ? "LONG GAMMA" : "SHORT GAMMA"}</h2>
      <p className="regime-detail">{compactCurrency.format(gamma.net_gamma)}</p>
    </section>
  );
}

// The one-line "LONG GAMMA +$18.4B" pill next to the price chart's own "En
// vivo" mode-pill (see price-chart.tsx) -- deliberately not a compact CSS
// variant of RegimeBadge above (that variant existed once, in the topbar,
// and was removed for good -- see globals.css's own comment on
// .pre-session-panel .regime-badge). This shows different information
// entirely (a net_gamma dollar amount RegimeBadge never displays, not
// price-vs-Gamma-Flip), so it's its own small component -- what IS reused
// is the regime-sign convention and the long/short color coding.
//
// Reads gamma.dealer_position (not market.dealer_mode, which RegimeBadge
// uses above): dealer_position is the same net_gamma sign the dollar
// amount itself comes from, so the label and the amount can never
// disagree. market.dealer_mode can legitimately differ from it when a
// price move already crossed Gamma Flip ahead of the next gamma recalc
// (see RegimeBadge's own "unconfirmed" handling above) -- a real
// distinction this compact badge deliberately doesn't take on, since
// Dashboard already only renders PriceChart once gamma/market both exist
// (no separate loading/freshness state to manage here).
export function RegimeCompactBadge({ gamma }: { gamma: GammaResponse }) {
  const isLong = gamma.dealer_position === "long_gamma";
  const label = isLong ? "LONG GAMMA" : "SHORT GAMMA";
  const amount = compactCurrency.format(gamma.net_gamma);
  return (
    <span
      className={`mode-pill regime-badge-compact ${isLong ? "long" : "short"}`}
      aria-label={`${label} ${amount}`}
    >
      {label} {amount}
    </span>
  );
}
