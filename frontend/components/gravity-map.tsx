import type { GammaResponse, MarketResponse } from "@/lib/types";

// Not wired into Dashboard since the TradingView-style layout (PR #56)
// superseded it with PriceChart's Static/Historical level lines — dead
// code, kept only for its own test coverage. Deliberately left out of the
// language-selector translation pass (its fixed Spanish text stays as-is)
// since translating unreachable UI isn't worth the upkeep; revisit if this
// component is ever wired back in.
type GravityMapProps = { gamma: GammaResponse; market: MarketResponse };

export const LEVEL_MERGE_THRESHOLD = 0.02;

function percentage(value: number, minimum: number, maximum: number) {
  if (maximum <= minimum) return 50;
  return Math.min(100, Math.max(0, ((value - minimum) / (maximum - minimum)) * 100));
}

function level(value: number) {
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export function GravityMap({ gamma, market }: GravityMapProps) {
  const range = gamma.call_wall - gamma.put_wall;
  const mergeLevels =
    range > 0 &&
    Math.abs(gamma.gamma_flip - gamma.absolute_gamma_strike) <
      LEVEL_MERGE_THRESHOLD * range;
  const position = (value: number) => `${percentage(value, gamma.put_wall, gamma.call_wall)}%`;

  return (
    <section className="panel gravity-panel" aria-labelledby="gravity-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Niveles vigentes</p>
          <h2 id="gravity-title">Mapa de gravitación</h2>
        </div>
        <span className="mode-pill">Estático</span>
      </div>
      <div className="gravity-map" aria-label={`Mapa de niveles para ${gamma.symbol}`}>
        <div className="gravity-track" />
        <div className="marker wall-marker put" style={{ left: "0%" }}>
          <span className="marker-line" />
          <span className="marker-label">Put Wall</span>
          <span className="marker-value">{level(gamma.put_wall)}</span>
        </div>
        <div className="marker wall-marker call" style={{ left: "100%" }}>
          <span className="marker-line" />
          <span className="marker-label">Call Wall</span>
          <span className="marker-value">{level(gamma.call_wall)}</span>
        </div>

        {mergeLevels ? (
          <div className="marker" style={{ left: position(gamma.gamma_flip) }}>
            <span className="marker-label">Flip / Abs. Gamma</span>
            <span className="marker-value">
              {level(gamma.gamma_flip)} / {level(gamma.absolute_gamma_strike)}
            </span>
            <span className="marker-line" />
          </div>
        ) : (
          <>
            <div className="marker" style={{ left: position(gamma.gamma_flip) }}>
              <span className="marker-label">Gamma Flip</span>
              <span className="marker-value">{level(gamma.gamma_flip)}</span>
              <span className="marker-line" />
            </div>
            <div className="marker" style={{ left: position(gamma.absolute_gamma_strike) }}>
              <span className="marker-label">Abs. Gamma</span>
              <span className="marker-value">{level(gamma.absolute_gamma_strike)}</span>
              <span className="marker-line" />
            </div>
          </>
        )}

        <div className="price-marker" style={{ left: position(market.price) }}>
          <span className="price-dot" />
          <span className="price-value">Precio {level(market.price)}</span>
        </div>
      </div>
    </section>
  );
}
