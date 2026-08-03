import type { GammaResponse, MarketResponse } from "@/lib/types";

type RegimeBadgeProps = { gamma: GammaResponse; market: MarketResponse };

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
});

const UNCONFIRMED_TOOLTIP =
  "El precio cruzó el Gamma Flip antes del último recálculo del agregado — régimen basado en precio.";

export function RegimeBadge({ gamma, market }: RegimeBadgeProps) {
  const isLong = market.dealer_mode === "long_gamma";
  const relation = market.price >= gamma.gamma_flip ? "arriba" : "debajo";
  const isConfirmed = market.dealer_mode_confirmed;

  return (
    <section
      className={`panel regime-badge ${isLong ? "long" : "short"}${
        isConfirmed ? "" : " unconfirmed"
      }`}
      aria-label="Régimen gamma"
      title={isConfirmed ? undefined : UNCONFIRMED_TOOLTIP}
    >
      <div>
        <p className="eyebrow">Régimen actual</p>
        {isConfirmed ? (
          <h2 className="regime-label">{isLong ? "LONG GAMMA" : "SHORT GAMMA"}</h2>
        ) : (
          <div className="regime-heading">
            <h2 className="regime-label">{isLong ? "LONG GAMMA" : "SHORT GAMMA"}</h2>
            <span
              className="regime-warning"
              role="img"
              aria-label="Régimen transitorio"
              title={UNCONFIRMED_TOOLTIP}
            >
              ⚠
            </span>
          </div>
        )}
      </div>
      <div>
        <p className="regime-detail">
          {gamma.symbol} {currency.format(market.price)} — {relation} del Flip (
          {currency.format(gamma.gamma_flip)})
        </p>
        <span className="regime-meta">Actualización cada 30 segundos</span>
      </div>
    </section>
  );
}
