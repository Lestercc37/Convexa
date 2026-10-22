import { cubeSide } from "@/lib/alert-cubes";
import { parseContractSide } from "@/lib/occ-symbol";
import type { MinuteFlowResponse, WhaleAlert } from "@/lib/types";

// "Flujo desde la apertura" (2026-10-22, Lester): the flow since the 09:30 open summed into one readable line, so the context is there at 09:35 instead of waiting for the
// alerts of 09:45. Context, not a forecast: bullish = buying a call or selling a put, bearish = selling a call or buying a put.

export type OpeningFlow = {
  /** Delta-equivalent contracts since the open, signed (positive = bullish pressure). */
  calls: number;
  puts: number;
  net: number;
  /** Mean share of the minute's volume that carried a side (0-1), null when no minute reports one. */
  classifiedShare: number | null;
  minutes: number;
};

export function summarizeOpeningFlow(response: MinuteFlowResponse | null, openSeconds: number): OpeningFlow | null {
  if (!response) return null;
  let calls = 0;
  let puts = 0;
  let minutes = 0;
  const shares: number[] = [];
  for (const point of response.minutes) {
    const seconds = Date.parse(point.minute) / 1000;
    if (!Number.isFinite(seconds) || seconds < openSeconds) continue;
    calls += point.calls_delta_flow;
    puts += point.puts_delta_flow;
    minutes += 1;
    if (point.classified_share !== null && Number.isFinite(point.classified_share)) shares.push(point.classified_share);
  }
  if (minutes === 0) return null;
  return {
    calls,
    puts,
    net: calls + puts,
    classifiedShare: shares.length > 0 ? shares.reduce((sum, value) => sum + value, 0) / shares.length : null,
    minutes,
  };
}

export type OpeningAlerts = {
  bullishPremium: number;
  bearishPremium: number;
  /** Delta-equivalent contracts (contracts x |delta|) of the alerts with a clear side. */
  bullishDeq: number;
  bearishDeq: number;
  bullishCount: number;
  bearishCount: number;
  unclearCount: number;
};

/** Alerts since the open, split by the direction their clear side implies; alerts without a clear side are only counted. */
export function summarizeOpeningAlerts(alerts: WhaleAlert[], openSeconds: number): OpeningAlerts {
  const out: OpeningAlerts = { bullishPremium: 0, bearishPremium: 0, bullishDeq: 0, bearishDeq: 0, bullishCount: 0, bearishCount: 0, unclearCount: 0 };
  for (const alert of alerts) {
    const seconds = Date.parse(alert.trade_start ?? alert.timestamp) / 1000;
    if (!Number.isFinite(seconds) || seconds < openSeconds) continue;
    const side = cubeSide(alert);
    const right = parseContractSide(alert.contract);
    if (side === "mixed" || right === null) {
      out.unclearCount += 1;
      continue;
    }
    const bullish = (side === "buy") === (right === "call");
    const deq = alert.contracts != null && alert.delta != null ? Math.round(alert.contracts * alert.delta) : 0;
    if (bullish) {
      out.bullishPremium += alert.amount;
      out.bullishDeq += deq;
      out.bullishCount += 1;
    } else {
      out.bearishPremium += alert.amount;
      out.bearishDeq += deq;
      out.bearishCount += 1;
    }
  }
  return out;
}

/** "+12,345" / "−4,567": delta-equivalent contracts with an explicit sign (a real minus sign, like the flow bars' colours say). */
export function formatDeltaFlow(value: number): string {
  const rounded = Math.round(Math.abs(value));
  if (rounded === 0) return "0";
  return `${value > 0 ? "+" : "\u2212"}${rounded.toLocaleString("en-US")}`;
}

/** "$12.3M" / "$450K" / "$0": premium in dollars, compact. */
export function formatPremium(value: number): string {
  if (!(value > 0)) return "$0";
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1)}M`;
  if (value >= 1_000) return `$${Math.round(value / 1_000)}K`;
  return `$${Math.round(value)}`;
}
