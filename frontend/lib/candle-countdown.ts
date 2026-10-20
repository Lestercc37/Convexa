import type { Timeframe } from "@/lib/candles";

// The time left in the candle that is forming, like the countdown under the last price on TradingView. Candles are bucketed by elapsed time (lib/candles.ts), so the end of the
// current candle is the next multiple of its size and does not depend on a candle having been created yet (the first tick of a new minute creates it a moment later).

const BUCKET_SECONDS: Record<Timeframe, number> = { "1m": 60, "5m": 300, "15m": 900, "1h": 3600 };
// A last candle that ended longer ago than this means no live data (market closed, feed stalled): no countdown rather than a number that keeps running over nothing.
const STALE_AFTER_SECONDS = 120;

export function candleSecondsLeft(lastCandleTime: number | null, timeframe: Timeframe, nowSeconds: number): number | null {
  if (lastCandleTime === null) return null;
  const size = BUCKET_SECONDS[timeframe];
  if (nowSeconds < lastCandleTime || nowSeconds >= lastCandleTime + size + STALE_AFTER_SECONDS) return null;
  return Math.ceil((Math.floor(nowSeconds / size) + 1) * size - nowSeconds);
}

export function formatCountdown(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = String(total % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${String(minutes).padStart(2, "0")}:${rest}`;
}
