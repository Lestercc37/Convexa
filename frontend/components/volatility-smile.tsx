"use client";

import { useEffect, useMemo, useState } from "react";
import { getOptionChain, getOptionChainExpirations } from "@/lib/api";
import { describeError } from "@/lib/i18n/describe-error";
import { useLanguage, type Language } from "@/lib/i18n/language-context";
import { currentEasternDate } from "@/lib/market-session";
import { POLLING_INTERVAL_MS } from "@/lib/polling";
import type { OptionChainResponse, OptionContract } from "@/lib/types";

type VolatilitySmileProps = {
  symbol: string;
  marketPrice: number;
};

// Strikes run up the left axis and IV across the bottom, so the strike ladder reads like the price
// chart beside it. Only the strikes within +-1.5% of spot are drawn: the far wings stretched the IV
// scale and flattened the part around the money that the panel is about.
const PLOT = { left: 50, right: 396, top: 12, bottom: 262 };
const WINDOW_FRACTION = 0.015;
const STRIKE_TICKS = 5;
const IV_TICKS = 4;

function nearestAtmStrike(contracts: OptionContract[], marketPrice: number): number | null {
  const strikes = [...new Set(contracts.map((contract) => contract.strike))];
  if (!strikes.length) return null;
  return strikes.reduce((nearest, strike) =>
    Math.abs(strike - marketPrice) < Math.abs(nearest - marketPrice) ? strike : nearest,
  );
}

// Only dates that have not expired yet (today or later, New York calendar): an expired date has
// nothing left to ask the provider about.
function currentExpirations(expirations: string[]): string[] {
  const today = currentEasternDate();
  return [...expirations].sort().filter((date) => date >= today);
}

// Contracts whose strike is within +-1.5% of spot; with no usable spot nothing is cut.
function withinWindow(contracts: OptionContract[], marketPrice: number): OptionContract[] {
  if (!Number.isFinite(marketPrice) || marketPrice <= 0) return contracts;
  const limit = marketPrice * WINDOW_FRACTION;
  return contracts.filter((contract) => Math.abs(contract.strike - marketPrice) <= limit);
}

function ticks(minimum: number, maximum: number, count: number): number[] {
  if (!(maximum > minimum)) return [minimum];
  return Array.from({ length: count }, (_, index) => minimum + ((maximum - minimum) * index) / (count - 1));
}

function scale(value: number, minimum: number, maximum: number, start: number, end: number) {
  if (maximum <= minimum) return (start + end) / 2;
  return start + ((value - minimum) / (maximum - minimum)) * (end - start);
}

const EXPIRATION_LOCALE: Record<Language, string> = { es: "es-US", en: "en-US" };

function expirationLabel(expiration: string, language: Language) {
  return new Intl.DateTimeFormat(EXPIRATION_LOCALE[language], {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${expiration}T00:00:00Z`));
}

export function VolatilitySmile({ symbol, marketPrice }: VolatilitySmileProps) {
  const { language, t } = useLanguage();
  const [expirations, setExpirations] = useState<string[]>([]);
  const [selectedExpiration, setSelectedExpiration] = useState("");
  const [expirationsLoaded, setExpirationsLoaded] = useState(false);
  const [chain, setChain] = useState<OptionChainResponse | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    const controller = new AbortController();
    // Only the distinct expiration dates are needed here, to populate the
    // dropdown below -- getOptionChainExpirations (not getOptionChain)
    // avoids fetching/serializing every contract's full greeks/OI/bid/ask
    // just to discard all of it but `.expiration` (confirmed live,
    // 2026-09-22: the full unscoped chain had grown to ~8,000 contracts /
    // 2.3MB for SPX after the Gamma Flip wide-search fix, PR #159, heavy
    // enough to help starve /chain/{symbol}'s shared threadpool during a
    // slow scheduler cycle).
    getOptionChainExpirations(symbol, controller.signal)
      .then((response) => {
        const available = currentExpirations(response.expirations);
        setExpirations(available);
        setSelectedExpiration(available[0] ?? "");
        setExpirationsLoaded(true);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason);
        }
      });
    return () => controller.abort();
  }, [symbol]);

  useEffect(() => {
    if (!selectedExpiration) return;
    const controller = new AbortController();
    // Polled like every other live panel on the dashboard (dashboard.tsx's
    // own 30s refresh) instead of fetching once and going stale until the
    // user changes symbol/expiration -- confirmed live, 2026-09-17: IV per
    // contract moves within a session same as everything else derived from
    // the option chain, so a smile frozen at mount time drifted from the
    // real market within minutes.
    const loadChain = () => {
      // The panel stayed open past the end of the selected date (e.g. overnight): do not
      // ask for the dead date again, pick the nearest current one instead.
      if (selectedExpiration < currentEasternDate()) {
        getOptionChainExpirations(symbol, controller.signal)
          .then((response) => {
            const available = currentExpirations(response.expirations);
            setExpirations(available);
            setSelectedExpiration(available[0] ?? "");
            if (!available.length) setChain(null);
          })
          .catch((reason: unknown) => {
            if (!controller.signal.aborted) setError(reason);
          });
        return;
      }
      getOptionChain(symbol, selectedExpiration, controller.signal)
        .then((response) => {
          setChain(response);
          setError(null);
        })
        .catch((reason: unknown) => {
          if (!controller.signal.aborted) {
            setError(reason);
          }
        });
    };
    loadChain();
    const interval = window.setInterval(loadChain, POLLING_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [selectedExpiration, symbol]);

  // No current expiration at all (e.g. the stored chain still lists only past dates until the
  // scheduler writes a new one): keep looking every poll instead of staying empty until a reload.
  useEffect(() => {
    if (!expirationsLoaded || selectedExpiration) return;
    const controller = new AbortController();
    const interval = window.setInterval(() => {
      getOptionChainExpirations(symbol, controller.signal)
        .then((response) => {
          const available = currentExpirations(response.expirations);
          if (!available.length) return;
          setExpirations(available);
          setSelectedExpiration(available[0]);
        })
        .catch(() => {});
    }, POLLING_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [expirationsLoaded, selectedExpiration, symbol]);

  const contracts = useMemo(() => chain?.contracts ?? [], [chain]);
  const plot = useMemo(() => {
    const windowed = withinWindow(contracts, marketPrice);
    // IV = 0 means the provider found no solution (deep in the money), not a 0% volatility: such
    // a contract is left off the plot and counted in the note instead of drawn at the bottom edge.
    const points = windowed.filter((contract) => contract.iv > 0);
    const impliedVolatilities = points.map((contract) => contract.iv);
    const hasWindow = Number.isFinite(marketPrice) && marketPrice > 0;
    const strikes = windowed.map((contract) => contract.strike);
    return {
      points,
      missingIvCount: windowed.length - points.length,
      minimumStrike: hasWindow ? marketPrice * (1 - WINDOW_FRACTION) : Math.min(...strikes),
      maximumStrike: hasWindow ? marketPrice * (1 + WINDOW_FRACTION) : Math.max(...strikes),
      minimumIv: impliedVolatilities.length ? Math.min(...impliedVolatilities) : 0,
      maximumIv: impliedVolatilities.length ? Math.max(...impliedVolatilities) : 1,
      atmStrike: nearestAtmStrike(windowed, marketPrice),
    };
  }, [contracts, marketPrice]);

  const x = (iv: number) => scale(iv, plot.minimumIv, plot.maximumIv, PLOT.left, PLOT.right);
  const y = (strike: number) =>
    scale(strike, plot.minimumStrike, plot.maximumStrike, PLOT.bottom, PLOT.top);
  const strikeTicks = ticks(plot.minimumStrike, plot.maximumStrike, STRIKE_TICKS).map(Math.round);
  const ivTicks = ticks(plot.minimumIv, plot.maximumIv, IV_TICKS);

  return (
    <section className="panel smile-panel" aria-labelledby="smile-title">
      <div className="panel-heading smile-heading">
        <div>
          <p className="eyebrow">{t.volatilitySmile.eyebrow}</p>
          <h2 id="smile-title">Volatility Smile</h2>
          <p className="smile-role">{t.volatilitySmile.roleSubtitle}</p>
        </div>
        <label className="expiration-control">
          <span>{t.volatilitySmile.expirationLabel}</span>
          <select
            value={selectedExpiration}
            onChange={(event) => setSelectedExpiration(event.target.value)}
            disabled={!expirations.length}
          >
            {expirations.map((expiration) => (
              <option key={expiration} value={expiration}>
                {expirationLabel(expiration, language)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error ? (
        <p className="smile-status error" role="alert">{describeError(error, t)}</p>
      ) : contracts.length ? (
        <div className="smile-chart-wrap">
          <svg
            className="smile-chart"
            viewBox="0 0 420 300"
            role="img"
            aria-label={t.volatilitySmile.chartAriaLabel(symbol, selectedExpiration)}
          >
            <line className="smile-axis" x1={PLOT.left} y1={PLOT.bottom} x2={PLOT.right} y2={PLOT.bottom} />
            <line className="smile-axis" x1={PLOT.left} y1={PLOT.top} x2={PLOT.left} y2={PLOT.bottom} />
            {strikeTicks.map((strike) => (
              <text key={`strike-${strike}`} className="smile-tick" x={PLOT.left - 6} y={y(strike) + 4} textAnchor="end">{strike}</text>
            ))}
            {ivTicks.map((iv) => (
              <text key={`iv-${iv}`} className="smile-tick" x={x(iv)} y={PLOT.bottom + 14}>{(iv * 100).toFixed(0)}%</text>
            ))}
            {plot.atmStrike !== null && (
              <g aria-label={t.volatilitySmile.atmAriaLabel(plot.atmStrike)}>
                <line className="smile-atm" x1={PLOT.left} y1={y(plot.atmStrike)} x2={PLOT.right} y2={y(plot.atmStrike)} />
                <text className="smile-atm-label" x={PLOT.right - 4} y={y(plot.atmStrike) - 4} textAnchor="end">ATM {plot.atmStrike}</text>
              </g>
            )}
            {plot.points.map((contract) => {
              const label = t.volatilitySmile.pointAriaLabel(
                contract.type,
                contract.strike,
                (contract.iv * 100).toFixed(2),
              );
              return (
                <circle
                  key={contract.occ_symbol}
                  className={`smile-point ${contract.type}`}
                  cx={x(contract.iv)}
                  cy={y(contract.strike)}
                  r="4"
                  aria-label={label}
                >
                  <title>{label}</title>
                </circle>
              );
            })}
            <text className="smile-axis-label" x={(PLOT.left + PLOT.right) / 2} y="294">IV</text>
            <text className="smile-axis-label" x="12" y={(PLOT.top + PLOT.bottom) / 2} transform={`rotate(-90 12 ${(PLOT.top + PLOT.bottom) / 2})`}>Strike</text>
          </svg>
          {plot.missingIvCount > 0 && (
            <p className="smile-note">{t.volatilitySmile.missingIvNote(plot.missingIvCount)}</p>
          )}
          <div className="smile-legend" aria-label={t.volatilitySmile.legendAriaLabel}>
            <span><i className="legend-dot call" /> Calls</span>
            <span><i className="legend-dot put" /> Puts</span>
          </div>
        </div>
      ) : (
        <p className="smile-status">
          {expirationsLoaded && !expirations.length
            ? t.volatilitySmile.noCurrentExpiration
            : t.volatilitySmile.loading}
        </p>
      )}
    </section>
  );
}
