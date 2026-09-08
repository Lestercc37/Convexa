import { useLanguage } from "@/lib/i18n/language-context";
import type { GammaResponse, MarketResponse } from "@/lib/types";

type RegimeBadgeProps = { gamma: GammaResponse; market: MarketResponse };

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
});

// "+$18.4B" / "-$29.5M" -- signDisplay makes a positive net_gamma show its
// own explicit "+" (Intl otherwise only marks negatives), notation:
// "compact" is what turns a real net_gamma (single- to low-double-digit
// billions for an index, tens of millions for a single stock -- confirmed
// against live gamma_aggregates: SPX ~$16.6B, AAPL ~$46M) into "B"/"M"
// instead of a wall of digits that would never fit a one-line pill.
const compactCurrency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
  signDisplay: "exceptZero",
});

export function RegimeBadge({ gamma, market }: RegimeBadgeProps) {
  const { t } = useLanguage();
  const isLong = market.dealer_mode === "long_gamma";
  const relation = market.price >= gamma.gamma_flip ? t.regimeBadge.above : t.regimeBadge.below;
  const isConfirmed = market.dealer_mode_confirmed;
  const unconfirmedTooltip = t.regimeBadge.unconfirmedTooltip;

  return (
    <section
      className={`panel regime-badge ${isLong ? "long" : "short"}${
        isConfirmed ? "" : " unconfirmed"
      }`}
      aria-label={t.regimeBadge.ariaLabel}
      title={isConfirmed ? undefined : unconfirmedTooltip}
    >
      <div>
        <p className="eyebrow">{t.regimeBadge.currentRegimeEyebrow}</p>
        {isConfirmed ? (
          <h2 className="regime-label">{isLong ? "LONG GAMMA" : "SHORT GAMMA"}</h2>
        ) : (
          <div className="regime-heading">
            <h2 className="regime-label">{isLong ? "LONG GAMMA" : "SHORT GAMMA"}</h2>
            <span
              className="regime-warning"
              role="img"
              aria-label={t.regimeBadge.transientAriaLabel}
              title={unconfirmedTooltip}
            >
              ⚠
            </span>
          </div>
        )}
      </div>
      <div>
        <p className="regime-detail">
          {t.regimeBadge.detail(
            gamma.symbol,
            currency.format(market.price),
            relation,
            currency.format(gamma.gamma_flip),
          )}
        </p>
        <span className="regime-meta">{t.regimeBadge.updateFrequency}</span>
      </div>
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
