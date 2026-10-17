import type { ISeriesApi, ITimeScaleApi, Logical, Time } from "lightweight-charts";

// lightweight-charts' timeScale().timeToCoordinate(time) only answers for a time that IS a bar of the series (confirmed against the real
// library, 2026-10-17: with 5-minute candles an alert at 14:43:03 got null). Alerts and heatmap columns have their own times, so this places an
// arbitrary time between the two bars around it: x = the nearest bar's x, moved toward the neighbouring bar in proportion to the time
// between them. Null when the time is not close to any bar (further than an hour beyond the ends of the series).
const BEYOND_THE_ENDS_SECONDS = 3600;

export function timeToX(
  timeScale: Pick<ITimeScaleApi<Time>, "timeToIndex" | "logicalToCoordinate">,
  series: Pick<ISeriesApi<"Candlestick">, "dataByIndex">,
  time: number,
): number | null {
  const index = timeScale.timeToIndex(time as Time, true);
  if (index === null) return null;
  const bar = series.dataByIndex(index);
  const x = timeScale.logicalToCoordinate(index as unknown as Logical);
  if (!bar || x === null || !Number.isFinite(x)) return null;
  const barTime = bar.time as number;
  if (time === barTime) return x;
  const neighbourIndex = time > barTime ? index + 1 : index - 1;
  const neighbour = series.dataByIndex(neighbourIndex);
  const neighbourX = timeScale.logicalToCoordinate(neighbourIndex as unknown as Logical);
  if (!neighbour || neighbourX === null || !Number.isFinite(neighbourX)) {
    return Math.abs(time - barTime) <= BEYOND_THE_ENDS_SECONDS ? x : null;
  }
  const span = (neighbour.time as number) - barTime;
  if (span === 0) return x;
  return x + ((time - barTime) / span) * (neighbourX - x);
}
