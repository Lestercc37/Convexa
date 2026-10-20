import type { Timeframe } from "@/lib/candles";
import type { MinuteFlowResponse } from "@/lib/types";

// The signed option delta flow per minute as two histogram series (calls and puts), bucketed to the candle timeframe so each bar sits under its candle. Positive = bullish pressure on both
// sides (buying a call, selling a put); negative = bearish (selling a call, buying a put): the same rule for both bars.

const TIMEFRAME_SECONDS: Record<Timeframe, number> = { "1m": 60, "5m": 300, "15m": 900, "1h": 3600 };
export const FLOW_UP_COLOR = "rgba(0, 200, 90, 0.85)";
export const FLOW_DOWN_COLOR = "rgba(255, 80, 86, 0.85)";

export type FlowBar = { time: number; value: number; color: string };
export type FlowBars = { calls: FlowBar[]; puts: FlowBar[] };

export function flowBarsFromResponse(response: MinuteFlowResponse, timeframe: Timeframe): FlowBars {
  const bucketSeconds = TIMEFRAME_SECONDS[timeframe];
  const calls = new Map<number, number>();
  const puts = new Map<number, number>();
  for (const point of response.minutes) {
    const seconds = Date.parse(point.minute) / 1000;
    if (!Number.isFinite(seconds)) continue;
    const bucket = Math.floor(seconds / bucketSeconds) * bucketSeconds;
    calls.set(bucket, (calls.get(bucket) ?? 0) + point.calls_delta_flow);
    puts.set(bucket, (puts.get(bucket) ?? 0) + point.puts_delta_flow);
  }
  const toBars = (buckets: Map<number, number>): FlowBar[] =>
    [...buckets.entries()]
      .sort((left, right) => left[0] - right[0])
      .map(([time, value]) => ({ time, value, color: value >= 0 ? FLOW_UP_COLOR : FLOW_DOWN_COLOR }));
  return { calls: toBars(calls), puts: toBars(puts) };
}
