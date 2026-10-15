import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

// GEX histogram aligned with the price axis: one horizontal bar per strike, drawn inside the chart pane
// against its right edge (next to the price scale), at the SAME vertical position the candles use
// (series.priceToCoordinate), so it follows zoom and vertical scrolling with the candles instead of
// being a second chart kept in sync by hand. Drawn at the bottom of the pane: candles and lines stay
// on top. Green = positive net gamma at the strike, red = negative.

export type GexBar = {
  strike: number;
  net: number;
  absolute: number;
};

export const GEX_HISTOGRAM_MAX_WIDTH_FRACTION = 0.22;
export const GEX_HISTOGRAM_POSITIVE_COLOR = "rgba(0, 220, 90, 0.38)";
export const GEX_HISTOGRAM_NEGATIVE_COLOR = "rgba(250, 0, 10, 0.38)";
const ROW_FILL_FRACTION = 0.72;
const MINIMUM_ROW_HEIGHT_BITMAP_PX = 1;

type DrawTarget = Parameters<IPrimitivePaneRenderer["draw"]>[0];

// Typical vertical distance, on screen, between adjacent strikes: the median, so one far-away strike or a
// strike scrolled out of view never stretches every bar.
export function medianRowStep(ys: number[]): number | null {
  const sorted = [...ys].sort((a, b) => a - b);
  const steps: number[] = [];
  for (let index = 1; index < sorted.length; index += 1) {
    const step = sorted[index] - sorted[index - 1];
    if (step > 0) steps.push(step);
  }
  if (!steps.length) return null;
  steps.sort((a, b) => a - b);
  return steps[Math.floor(steps.length / 2)];
}

class GexHistogramRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly source: GexHistogramPrimitive) {}

  draw(target: DrawTarget): void {
    target.useBitmapCoordinateSpace((scope) => {
      const series = this.source.series;
      const bars = this.source.bars;
      if (!series || !bars.length) return;
      const largest = Math.max(...bars.map((bar) => bar.absolute));
      if (!(largest > 0)) return;

      const placed: { y: number; bar: GexBar }[] = [];
      for (const bar of bars) {
        const y = series.priceToCoordinate(bar.strike);
        if (y === null || !Number.isFinite(y)) continue;
        placed.push({ y: y * scope.verticalPixelRatio, bar });
      }
      if (!placed.length) return;
      const step = medianRowStep(placed.map((entry) => entry.y));
      const rowHeight = Math.max(
        MINIMUM_ROW_HEIGHT_BITMAP_PX,
        (step ?? scope.verticalPixelRatio * 6) * ROW_FILL_FRACTION,
      );
      const maxWidth = scope.bitmapSize.width * GEX_HISTOGRAM_MAX_WIDTH_FRACTION;

      for (const { y, bar } of placed) {
        const width = (bar.absolute / largest) * maxWidth;
        if (width < 1) continue;
        scope.context.fillStyle = bar.net >= 0 ? GEX_HISTOGRAM_POSITIVE_COLOR : GEX_HISTOGRAM_NEGATIVE_COLOR;
        scope.context.fillRect(scope.bitmapSize.width - width, y - rowHeight / 2, width, rowHeight);
      }
    });
  }
}

export class GexHistogramPrimitive implements ISeriesPrimitive<Time> {
  series: ISeriesApi<"Candlestick"> | null = null;
  bars: GexBar[] = [];
  private requestUpdate: (() => void) | null = null;
  private readonly view: IPrimitivePaneView;

  constructor() {
    const renderer = new GexHistogramRenderer(this);
    this.view = { zOrder: () => "bottom", renderer: () => renderer };
  }

  attached(parameter: SeriesAttachedParameter<Time>): void {
    this.series = parameter.series as ISeriesApi<"Candlestick">;
    this.requestUpdate = parameter.requestUpdate;
  }

  detached(): void {
    this.series = null;
    this.requestUpdate = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  setBars(bars: GexBar[]): void {
    this.bars = bars;
    this.requestUpdate?.();
  }
}
