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
