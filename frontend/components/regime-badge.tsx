import { useLanguage } from "@/lib/i18n/language-context";
import type { GammaResponse, MarketResponse } from "@/lib/types";

type RegimeBadgeProps = { gamma: GammaResponse; market: MarketResponse };

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
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
