import { currentEasternDate } from "@/lib/market-session";
import type { OptionContract } from "@/lib/types";
import type { SmilePoint } from "@/components/live-profile-primitive";

// What the Volatility Smile panel and the smile dots drawn on the chart (live-profile-primitive.ts) share: which contracts are readable and which window around spot is shown.

/** Only the strikes within +-1.5% of spot are drawn: the far wings stretched the IV scale and flattened the part around the money. */
export const WINDOW_FRACTION = 0.015;

export function nearestAtmStrike(contracts: OptionContract[], marketPrice: number): number | null {
  const strikes = [...new Set(contracts.map((contract) => contract.strike))];
  if (!strikes.length) return null;
  return strikes.reduce((nearest, strike) =>
    Math.abs(strike - marketPrice) < Math.abs(nearest - marketPrice) ? strike : nearest,
  );
}

// Only dates that have not expired yet (today or later, New York calendar): an expired date has
// nothing left to ask the provider about.
export function currentExpirations(expirations: string[]): string[] {
  const today = currentEasternDate();
  return [...expirations].sort().filter((date) => date >= today);
}

// On a day when two roots list the same expiration (SPX's AM-settled monthly and SPXW's PM-settled weekly on the third Friday), the
// same strike and right appears twice and the plot draws two smiles on top of each other -- the AM one with stale, very wide quotes.
// Keep one contract per strike and right: the one with the tighter relative bid/ask spread (the live one).
export function relativeSpread(contract: OptionContract): number {
  const mid = (contract.bid + contract.ask) / 2;
  if (!(contract.ask >= contract.bid) || !(mid > 0)) return Number.POSITIVE_INFINITY;
  return (contract.ask - contract.bid) / mid;
}

export function onePerStrikeAndRight(contracts: OptionContract[]): OptionContract[] {
  const best = new Map<string, OptionContract>();
  for (const contract of contracts) {
    const key = `${contract.strike}|${contract.type}`;
    const current = best.get(key);
    if (current === undefined || relativeSpread(contract) < relativeSpread(current)) best.set(key, contract);
  }
  return contracts.filter((contract) => best.get(`${contract.strike}|${contract.type}`) === contract);
}

// An in-the-money contract whose price barely exceeds its intrinsic value carries no readable IV: the time value is smaller than
// the bid/ask can resolve, so the solved IV is noise (NDX 2026-10-16: puts 280-410 pts in the money with 10% spreads showed IV 21-22%
// against 30-37% for their neighbours and drew stray dots). Keep a contract only when its time value is at least half its spread.
export function hasReadableIv(contract: OptionContract, marketPrice: number): boolean {
  if (!Number.isFinite(marketPrice) || marketPrice <= 0) return true;
  if (!(contract.ask >= contract.bid) || !(contract.ask > 0)) return true;
  const intrinsic = contract.type === "call" ? Math.max(marketPrice - contract.strike, 0) : Math.max(contract.strike - marketPrice, 0);
  const mid = (contract.bid + contract.ask) / 2;
  return mid - intrinsic >= (contract.ask - contract.bid) / 2;
}

// Contracts whose strike is within +-1.5% of spot; with no usable spot nothing is cut.
export function withinWindow(contracts: OptionContract[], marketPrice: number): OptionContract[] {
  if (!Number.isFinite(marketPrice) || marketPrice <= 0) return contracts;
  const limit = marketPrice * WINDOW_FRACTION;
  return contracts.filter((contract) => Math.abs(contract.strike - marketPrice) <= limit);
}

/**
 * One dot per contract of the window with an IV, calls and puts at every strike, in and out of the money (as the old smile panel showed them): IV = 0 means the provider found no
 * solution (deep in the money), not a 0% volatility, so it is left off. An in-the-money contract whose time value is below its spread (hasReadableIv false) still gets its dot but is
 * flagged `reliable: false` (drawn hollow and fainter, left out of the IV scale) so the noise of those quotes cannot pass for a real part of the smile.
 */
export function smilePointsFromChain(contracts: OptionContract[], marketPrice: number): SmilePoint[] {
  return withinWindow(onePerStrikeAndRight(contracts), marketPrice)
    .filter((contract) => contract.iv > 0)
    .map((contract) => {
      const point: SmilePoint = { strike: contract.strike, iv: contract.iv, type: contract.type };
      if (!hasReadableIv(contract, marketPrice)) point.reliable = false;
      return point;
    });
}
