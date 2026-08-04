export type PricePoint = {
  timestamp: string;
  price: number;
};

export type MinuteCandle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
};

const SECONDS_PER_MINUTE = 60;

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
