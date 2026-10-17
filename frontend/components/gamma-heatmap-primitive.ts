import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";
import type { GammaHeatmapResponse } from "@/lib/types";

// Gamma heatmap: one rectangle per (time bucket, strike), drawn BEHIND the candles at the same coordinates they use
// (timeScale().timeToCoordinate / series.priceToCoordinate), so it follows zoom and scrolling with the chart instead of being a
// second chart kept in sync by hand. Same attachPrimitive pattern as the GEX histogram.
//
// Color = the stored per-strike figure of that bucket, scaled by the 98th percentile of |value| over the whole matrix (the API sends it as
// scale_p98): values at or above it get the top of the ramp, which is WHITE, so the strike(s) with the most exposure of the moment read as the
// brightest cells. Positive = green ramp, negative = red ramp. "Absolute gamma" has no sign and uses the green ramp.

export type HeatmapColumn = {
  /** Unix seconds of the column (floored to the minute so it lands on a candle). */
  time: number;
  /** One value per entry of `strikes`, same order; null = no stored value. */
  values: (number | null)[];
};

export type HeatmapData = {
  strikes: number[];
  columns: HeatmapColumn[];
  scale: number | null;
};

/** The API response as the primitive wants it: columns floored to the minute so they land on a candle. */
export function heatmapFromResponse(response: GammaHeatmapResponse): HeatmapData {
  return {
    strikes: response.strikes,
    scale: response.scale_p98,
    columns: response.columns.map((column) => ({
      time: Math.floor(Date.parse(column.as_of) / 60_000) * 60,
      values: column.values,
    })),
  };
}

type Stop = [number, number, number];

// Same ramps as the approved prototype (docs/prototipos/heatmap-flujo-alertas-2026-10-16.html).
export const HEATMAP_POSITIVE_STOPS: Stop[] = [[10, 61, 34], [8, 120, 60], [0, 168, 70], [0, 220, 90], [110, 255, 160], [255, 255, 255]];
export const HEATMAP_NEGATIVE_STOPS: Stop[] = [[74, 10, 13], [130, 8, 14], [190, 6, 12], [250, 0, 10], [255, 120, 110], [255, 255, 255]];

// Below this share of the scale a cell is left empty: the background of a quiet strike is the chart, not a dark tile.
export const HEATMAP_MINIMUM_SHARE = 0.04;
export const HEATMAP_TOP_SHARE = 0.985;
export const HEATMAP_ALPHA = 0.62;
const ROW_FILL_FRACTION = 0.92;
const MINIMUM_ROW_HEIGHT_BITMAP_PX = 1;

type DrawTarget = Parameters<IPrimitivePaneRenderer["draw"]>[0];

export function rampColor(stops: Stop[], fraction: number): [number, number, number] {
  const clamped = Math.min(1, Math.max(0, fraction));
  const position = clamped * (stops.length - 1);
  const lower = Math.floor(position);
  const upper = Math.min(stops.length - 1, lower + 1);
  const share = position - lower;
  return [
    Math.round(stops[lower][0] + (stops[upper][0] - stops[lower][0]) * share),
    Math.round(stops[lower][1] + (stops[upper][1] - stops[lower][1]) * share),
    Math.round(stops[lower][2] + (stops[upper][2] - stops[lower][2]) * share),
  ];
}

/** CSS color of a cell, or null when it is too faint to draw. */
export function cellColor(value: number | null, scale: number | null): string | null {
  if (value === null || scale === null || !(scale > 0)) return null;
  const share = Math.abs(value) / scale;
  if (share < HEATMAP_MINIMUM_SHARE) return null;
  const stops = value >= 0 ? HEATMAP_POSITIVE_STOPS : HEATMAP_NEGATIVE_STOPS;
  const [red, green, blue] = rampColor(stops, share >= HEATMAP_TOP_SHARE ? 1 : Math.min(1, share) * 0.96);
  return `rgba(${red}, ${green}, ${blue}, ${HEATMAP_ALPHA})`;
}

// Typical vertical distance between adjacent strikes on screen (median, so one far strike never stretches every row).
function medianStep(values: number[]): number | null {
  const sorted = [...values].sort((a, b) => a - b);
  const steps: number[] = [];
  for (let index = 1; index < sorted.length; index += 1) {
    const step = sorted[index] - sorted[index - 1];
    if (step > 0) steps.push(step);
  }
  if (!steps.length) return null;
  steps.sort((a, b) => a - b);
  return steps[Math.floor(steps.length / 2)];
}

class GammaHeatmapRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly source: GammaHeatmapPrimitive) {}

  draw(target: DrawTarget): void {
    target.useBitmapCoordinateSpace((scope) => {
      const { series, chart, data } = this.source;
      if (!series || !chart || !data || !data.columns.length || !data.strikes.length) return;
      const timeScale = chart.timeScale();

      const rows: { index: number; y: number }[] = [];
      data.strikes.forEach((strike, index) => {
        const y = series.priceToCoordinate(strike);
        if (y !== null && Number.isFinite(y)) rows.push({ index, y: y * scope.verticalPixelRatio });
      });
      if (!rows.length) return;
      const step = medianStep(rows.map((row) => row.y));
      const rowHeight = Math.max(MINIMUM_ROW_HEIGHT_BITMAP_PX, (step ?? scope.verticalPixelRatio * 6) * ROW_FILL_FRACTION);

      const xs = data.columns.map((column) => timeScale.timeToCoordinate(column.time as Time));
      let lastWidth = 0;
      data.columns.forEach((column, position) => {
        const x = xs[position];
        if (x === null || !Number.isFinite(x)) return;
        const next = xs[position + 1];
        const left = x * scope.horizontalPixelRatio;
        const width =
          next !== null && next !== undefined && Number.isFinite(next) && next > x
            ? (next - x) * scope.horizontalPixelRatio
            : lastWidth || scope.horizontalPixelRatio * 8;
        lastWidth = width;
        for (const row of rows) {
          const color = cellColor(column.values[row.index] ?? null, data.scale);
          if (color === null) continue;
          scope.context.fillStyle = color;
          scope.context.fillRect(left, row.y - rowHeight / 2, Math.max(1, width), rowHeight);
        }
      });
    });
  }
}

export class GammaHeatmapPrimitive implements ISeriesPrimitive<Time> {
  series: ISeriesApi<"Candlestick"> | null = null;
  chart: IChartApi | null = null;
  data: HeatmapData | null = null;
  private requestUpdate: (() => void) | null = null;
  private readonly view: IPrimitivePaneView;

  constructor() {
    const renderer = new GammaHeatmapRenderer(this);
    this.view = { zOrder: () => "bottom", renderer: () => renderer };
  }

  attached(parameter: SeriesAttachedParameter<Time>): void {
    this.series = parameter.series as ISeriesApi<"Candlestick">;
    this.chart = parameter.chart;
    this.requestUpdate = parameter.requestUpdate;
  }

  detached(): void {
    this.series = null;
    this.chart = null;
    this.requestUpdate = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  setData(data: HeatmapData | null): void {
    this.data = data;
    this.requestUpdate?.();
  }
}
