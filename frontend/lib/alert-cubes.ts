import { parseContractSide } from "@/lib/occ-symbol";
import type { WhaleAlert } from "@/lib/types";

// Alert cubes on the price chart (docs/diseno-heatmap-gamma-2026-10-14.txt, 12.8 to 12.14): one small square per whale alert, x = the alert's
// time, y = the contract's strike. Size and filter are delta-equivalent contracts (contracts x |delta|, by default only its net, one-sided
// part), color is the estimated side, and only the tooltip carries text. Pure functions here; the drawing is alert-cubes-primitive.ts.

export type CubeSide = "buy" | "sell" | "mixed";
/**
 * Three layers (Lester 2026-10-26): "clear" = the alert's flow has a readable side (simple / sweep trades, signed by the quote rule) -> colored square,
 * on by default; "package" = mostly multi-leg legs (codes 130-144), whose side and net bet one leg cannot tell -> neutral ring, own layer, off by
 * default; "big" = a large trade without a readable side (no quote, balanced, auction / cross / floor prints) -> neutral circle, own layer, off by
 * default. Packages and big trades never add to the buy or sell side.
 */
export type CubeKind = "clear" | "package" | "big";
export type CubeZone = "ITM" | "ATM" | "OTM" | "FAR";

export type AlertCube = {
  id: string;
  /** Unix seconds of the alert. */
  time: number;
  strike: number;
  right: "C" | "P";
  /** Delta-equivalent contracts: contracts x |delta|, rounded (the gross size, as the tooltip shows it). */
  deq: number;
  /** The net part of it: deq x |buy - sell| / (buy + sell); equal to deq when the alert had no quote to sign against (see netDeltaContracts). */
  netDeq: number;
  /** Side length in CSS pixels (6 / 9 / 12), from the net or the gross size depending on the filter's sizing. */
  size: number;
  side: CubeSide;
  kind: CubeKind;
  zone: CubeZone;
  alert: WhaleAlert;
};

/**
 * What sizes a cube and what the minimum is compared with. "gross": every delta contract traded. "net" (Lester 2026-10-23, walls of
 * balanced cubes at the window-fill minute): only the part that leaned to one side, so a 50/50 alert shrinks and a one-sided one keeps
 * its size. The color rule does not change.
 */
export type CubeSizing = "net" | "gross";

export type CubeFilter = {
  /** Minimum delta-equivalent contracts (the design default is 200), compared with the size the sizing picks. */
  minDeq: number;
  /** "Lejos" (|delta| outside 0.25-0.85) is hidden unless this is on. */
  showFar: boolean;
  /** Gross when absent (the behavior before the net option). */
  sizing?: CubeSizing;
  /** Layers to draw; only the clear-side squares when absent. */
  kinds?: Partial<Record<CubeKind, boolean>>;
};

export const DEFAULT_CUBE_FILTER: CubeFilter = { minDeq: 200, showFar: false };
/** What the chart starts with (the viewer can switch it off, see the "Neto" toggle). */
export const DEFAULT_CUBE_SIZING: CubeSizing = "net";

/**
 * Net delta-equivalent contracts: contracts x |delta| x |buy - sell| / (buy + sell), rounded; null when the alert cannot be sized. An alert
 * whose trades had no quote at all (quote_unavailable) has no measured split, so it keeps its gross size: flow that could not be signed is
 * never shrunk away, it is drawn as before (no clear side).
 */
export function netDeltaContracts(
  alert: Pick<WhaleAlert, "contracts" | "delta" | "estimated_buy_volume" | "estimated_sell_volume" | "quote_unavailable">,
): number | null {
  if (alert.delta == null || alert.contracts == null) return null;
  const gross = alert.contracts * Math.abs(alert.delta);
  const total = alert.estimated_buy_volume + alert.estimated_sell_volume;
  if (alert.quote_unavailable || !(total > 0)) return Math.round(gross);
  return Math.round((gross * Math.abs(alert.estimated_buy_volume - alert.estimated_sell_volume)) / total);
}

// Relative to the minimum in use (item 12b): from the minimum up to twice it small, twice to three times medium, three times and up large (with the old 200: 200-400 / 400-600 / 600+).
export function cubeSizePx(deq: number, minimum: number = DEFAULT_CUBE_FILTER.minDeq): number {
  if (deq >= 3 * minimum) return 12;
  if (deq >= 2 * minimum) return 9;
  return 6;
}

// Buy share of the estimated flow: 60% or more = buy, 40% or less = sell, in between (or no quote) = no clear side.
export function cubeSide(alert: Pick<WhaleAlert, "estimated_buy_volume" | "estimated_sell_volume" | "quote_unavailable">): CubeSide {
  const total = alert.estimated_buy_volume + alert.estimated_sell_volume;
  if (alert.quote_unavailable || !(total > 0)) return "mixed";
  const share = alert.estimated_buy_volume / total;
  if (share >= 0.6) return "buy";
  if (share <= 0.4) return "sell";
  return "mixed";
}

/** Which layer an alert belongs to (see CubeKind): mostly multi-leg premium = package; a buy / sell side read from simple or sweep trades = clear; anything else = big. */
export function cubeKind(alert: Pick<WhaleAlert, "estimated_buy_volume" | "estimated_sell_volume" | "quote_unavailable" | "condition_premium">): CubeKind {
  const shares = conditionShares(alert.condition_premium);
  const share = (category: ConditionCategory) => shares?.find((entry) => entry.category === category)?.percent ?? 0;
  if (shares && share("multileg") >= 50) return "package";
  if (cubeSide(alert) === "mixed") return "big";
  if (shares && share("simple") + share("sweep") < 50) return "big";
  return "clear";
}

// OCC symbol {root}{YYMMDD}{C|P}{8-digit strike in thousandths}.
export function occStrike(occSymbol: string): number | null {
  const digits = occSymbol.slice(-8);
  if (!/^\d{8}$/.test(digits)) return null;
  return Number(digits) / 1000;
}

/**
 * The cubes to draw for a list of alerts. An alert without delta or contracts (written before migration 0046, or from the BVC path) cannot be
 * sized and is left out; so is one under the minimum size (net or gross, see CubeSizing), and a "Lejos" one unless asked for.
 */
export function buildCubes(alerts: WhaleAlert[], filter: CubeFilter = DEFAULT_CUBE_FILTER): AlertCube[] {
  const cubes: AlertCube[] = [];
  for (const alert of alerts) {
    if (alert.delta == null || alert.contracts == null) continue;
    const strike = occStrike(alert.contract);
    const side = parseContractSide(alert.contract);
    if (strike === null || side === null) continue;
    const kind = cubeKind(alert);
    if (!(filter.kinds ?? { clear: true })[kind]) continue;
    const deq = Math.round(alert.contracts * alert.delta);
    const netDeq = netDeltaContracts(alert) ?? deq;
    // A package or a big trade has no measured net side: it is sized by its gross delta (contracts x |delta|).
    const sizedBy = kind === "clear" && filter.sizing === "net" ? netDeq : deq;
    if (sizedBy < filter.minDeq) continue;
    if (alert.moneyness === "FAR" && !filter.showFar) continue;
    const time = Math.floor(Date.parse(alert.timestamp) / 1000);
    if (!Number.isFinite(time)) continue;
    cubes.push({
      id: `${alert.contract}|${alert.type}|${alert.timestamp}`,
      time,
      strike,
      right: side === "call" ? "C" : "P",
      deq,
      netDeq,
      size: cubeSizePx(sizedBy, filter.minDeq),
      side: cubeSide(alert),
      kind,
      zone: alert.moneyness,
      alert,
    });
  }
  return cubes;
}

export type ConditionCategory = "simple" | "sweep" | "auction" | "cross" | "floor" | "multileg" | "cancel" | "other";

// OPRA / ThetaData trade-condition code -> category (docs/whale-alerts-multileg-condition.md, ThetaData's table): 18 auto execution, 95
// intermarket sweep, 125/126 single-leg auction, 127/128 single-leg cross, 129 single-leg floor trade, 130-144 multi-leg, 40-44 and 148 cancels
// / extended hours.
export function conditionCategory(code: number): ConditionCategory {
  if (code === 18) return "simple";
  if (code === 95) return "sweep";
  if (code === 125 || code === 126) return "auction";
  if (code === 127 || code === 128) return "cross";
  if (code === 129) return "floor";
  if (code >= 130 && code <= 144) return "multileg";
  if ((code >= 40 && code <= 44) || code === 148) return "cancel";
  return "other";
}

/** Premium share (whole percent) per condition category, largest first; null when the alert has no split. */
export function conditionShares(conditionPremium: Record<string, number> | null | undefined): { category: ConditionCategory; percent: number }[] | null {
  if (!conditionPremium) return null;
  const totals = new Map<ConditionCategory, number>();
  let sum = 0;
  for (const [code, premium] of Object.entries(conditionPremium)) {
    const category = conditionCategory(Number(code));
    totals.set(category, (totals.get(category) ?? 0) + premium);
    sum += premium;
  }
  if (!(sum > 0)) return null;
  return [...totals.entries()]
    .map(([category, premium]) => ({ category, percent: Math.round((100 * premium) / sum) }))
    .sort((a, b) => b.percent - a.percent);
}

export type TrialLabel = "buy" | "sell" | "mixed" | "none";

// The second label of the two-label trial (design 12.14): the same 60/40 rule over the premium of the simple-condition trades only; "none" when
// those trades are under half of the alert's premium.
export function trialLabel(simpleFlow: { buy: number; sell: number } | null | undefined, amount: number): TrialLabel | null {
  if (!simpleFlow) return null;
  const signed = simpleFlow.buy + simpleFlow.sell;
  if (!(amount > 0) || signed < 0.5 * amount) return "none";
  const share = simpleFlow.buy / signed;
  if (share >= 0.6) return "buy";
  if (share <= 0.4) return "sell";
  return "mixed";
}
