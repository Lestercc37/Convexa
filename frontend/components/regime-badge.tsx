import type { GammaResponse, MarketResponse } from "@/lib/types";

type RegimeBadgeProps = { gamma: GammaResponse; market: MarketResponse };

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
});

export function RegimeBadge({ gamma, market }: RegimeBadgeProps) {
  const isLong = gamma.dealer_position === "long_gamma";
  const relation = market.price >= gamma.gamma_flip ? "arriba" : "debajo";

  return (
    <section
      className={`panel regime-badge ${isLong ? "long" : "short"}`}
      aria-label="Régimen gamma"
    >
      <div>
        <p className="eyebrow">Régimen actual</p>
        <h2 className="regime-label">{isLong ? "LONG GAMMA" : "SHORT GAMMA"}</h2>
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
