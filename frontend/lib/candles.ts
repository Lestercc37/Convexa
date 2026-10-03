export type PricePoint = {
  timestamp: string;
  price: number;
};

export type VwapPoint = {
  timestamp: string;
  value: number;
};

export type MinuteCandle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
};

const SECONDS_PER_MINUTE = 60;

export type Timeframe = "1m" | "5m" | "15m" | "1h";

const TIMEFRAME_MINUTES: Record<Timeframe, number> = {
  "1m": 1,
  "5m": 5,
  "15m": 15,
  "1h": 60,
};

// Buckets by elapsed time (`Math.floor(time / bucketSeconds)`), not by
// grouping every N sequential candles — so a gap in the underlying 1-minute
// data doesn't shift later buckets out of alignment with wall-clock time.
// `candles` must already be ascending by `time` (the contract
// `aggregateMinuteCandles` above already returns), since callers on the
// live chart require the same strictly-ascending order lightweight-charts
// needs.
export function aggregateCandles(candles: MinuteCandle[], timeframe: Timeframe): MinuteCandle[] {
  const minutes = TIMEFRAME_MINUTES[timeframe];
  if (minutes <= 1) return candles;

  const bucketSeconds = minutes * SECONDS_PER_MINUTE;
  const buckets = new Map<number, MinuteCandle>();

  for (const candle of candles) {
    const bucketStart = Math.floor(candle.time / bucketSeconds) * bucketSeconds;
    const existing = buckets.get(bucketStart);
    if (existing) {
      existing.high = Math.max(existing.high, candle.high);
      existing.low = Math.min(existing.low, candle.low);
      existing.close = candle.close;
    } else {
      buckets.set(bucketStart, {
        time: bucketStart,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      });
    }
  }

  return [...buckets.values()];
}

// Downsamples VWAP points to the same 1-per-minute resolution as
// aggregateMinuteCandles above, keeping the last observed value in each
// minute (closest analogue to a candle's `close`). VWAP points otherwise
// arrive at raw tick/poll resolution (a point every few seconds) and get
// fed to a lightweight-charts LineSeries sharing the *same* timeScale as
// the 1-minute candlestick series -- confirmed live, 2026-09-18: that
// resolution mismatch inflates the chart's shared logical-index space by
// ~40x (one index per VWAP tick instead of per minute), so fitContent()
// can no longer fit a full trading day's candles into a normal container
// width and silently collapses to showing only the most recent slice.
// `timestamp` is normalized to the bucket's own minute boundary (not the
// last raw tick's exact second) so a VWAP point and its same-minute
// candle land on the *exact* same logical time-grid position.
//
// `timeframe` extends the same rule to the coarser candle timeframes: the
// VWAP line must land on the *same* time grid as the candles it shares an
// axis with, not a finer one. Confirmed live, 2026-10-03 (user report:
// 5m/15m candles "muy separadas" / "una raya"), measured against the real
// lightweight-charts 5.2.0: 78 5m candles plus a 1-per-minute VWAP series
// give ~391 logical slots, so fitContent() sizes a slot for the 1-minute
// grid (3.4px in a 1400px chart) and each 5m candle -- one slot wide --
// ends up ~2.7px thick with ~17px of empty axis to its neighbour (15m:
// ~51px apart). Bucketing the VWAP to the candle timeframe makes
// candle-to-candle distance equal the slot width again (17px / 50px, bodies
// touching), the same joined look 1m has.
export function aggregateVwapPoints(points: VwapPoint[], timeframe: Timeframe = "1m"): VwapPoint[] {
  const bucketSeconds = TIMEFRAME_MINUTES[timeframe] * SECONDS_PER_MINUTE;
  const sortedPoints = points
    .map((point, index) => ({ ...point, index, time: Date.parse(point.timestamp) }))
    .filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value))
    .sort((left, right) => left.time - right.time || left.index - right.index);
  const lastValueByBucket = new Map<number, number>();

  for (const point of sortedPoints) {
    const bucket = Math.floor(point.time / 1000 / bucketSeconds) * bucketSeconds;
    lastValueByBucket.set(bucket, point.value);
  }

  return [...lastValueByBucket.entries()].map(([bucket, value]) => ({
    timestamp: new Date(bucket * 1000).toISOString(),
    value,
  }));
}

export function aggregateMinuteVwapPoints(points: VwapPoint[]): VwapPoint[] {
  return aggregateVwapPoints(points, "1m");
}

export function aggregateMinuteCandles(points: PricePoint[]): MinuteCandle[] {
  const sortedPoints = points
    .map((point, index) => ({ ...point, index, time: Date.parse(point.timestamp) }))
    .filter((point) => Number.isFinite(point.time) && Number.isFinite(point.price))
    .sort((left, right) => left.time - right.time || left.index - right.index);
  const candles = new Map<number, MinuteCandle>();

  for (const point of sortedPoints) {
    const minute = Math.floor(point.time / 1000 / SECONDS_PER_MINUTE) * SECONDS_PER_MINUTE;
    const candle = candles.get(minute);
    if (candle) {
      candle.high = Math.max(candle.high, point.price);
      candle.low = Math.min(candle.low, point.price);
      candle.close = point.price;
    } else {
      candles.set(minute, {
        time: minute,
        open: point.price,
        high: point.price,
        low: point.price,
        close: point.price,
      });
    }
  }

  return [...candles.values()];
}
