import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";
import { rampRgb, type HeatmapData, type HeatmapPalette } from "./gamma-heatmap-primitive";

// The "line of time" of the approved mockup (docs/prototipos/heatmap-flujo-alertas-2026-10-16.html, design 9 and 10): a dashed vertical line, AHORA, with the
// history (heatmap and candles) on its left and, on its right, ONE live profile with a bar per strike. The chart reserves the column's width at the right of the time
// scale (price-chart.tsx sets timeScale.rightOffsetPixels from profileWidthCssPx), so the candles stop at the line and nothing is drawn over the bars.
// Everything is placed with series.priceToCoordinate, the same conversion the candles use, so it follows zoom and vertical scrolling.
// The Volatility Smile rides on the same line: one dot per strike, IV of calls (green) and puts (red) running left from the line (the higher the IV, the closer to
// the line, so IV grows to the right as in the rest of Convexa's smile), aligned with the price axis.

export type ProfileBar = {
  strike: number;
  /** The signed figure of the strike (net gamma, delta, vanna or charm). */
  net: number;
  /** |net| (absolute gamma is its own figure and is passed as is). */
  absolute: number;
  /** Sides view: call gamma (positive) and put gamma (negative) of the strike, each drawn as its own bar; undefined = that side is off. */
  call?: number;
  put?: number;
};

export type SmilePoint = { strike: number; iv: number; type: "call" | "put" };

const COLUMN_MIN_PANE_WIDTH_CSS_PX = 520;
const COLUMN_WIDTH_FRACTION = 0.2;
const COLUMN_MIN_CSS_PX = 150;
const COLUMN_MAX_CSS_PX = 320;
const BAR_INSET_CSS_PX = 6;
const ROW_FILL_FRACTION = 0.78;
const MINIMUM_ROW_HEIGHT_BITMAP_PX = 1;
const SMILE_GAP_CSS_PX = 14;
const SMILE_BAND_CSS_PX = 92;
const SMILE_DOT_CSS_PX = 2.4;
export const LIVE_PROFILE_TOP_SHARE = 0.98;
export const AHORA_LINE_COLOR = "rgba(209, 212, 220, 0.78)";
const CALL_DOT_COLOR = "rgba(0, 220, 90, 0.92)";
const PUT_DOT_COLOR = "rgba(255, 92, 96, 0.92)";
const LABEL_COLOR = "rgba(209, 212, 220, 0.9)";
const DIM_LABEL_COLOR = "rgba(150, 156, 170, 0.9)";
const FONT_FAMILY = 'Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif';

type DrawTarget = Parameters<IPrimitivePaneRenderer["draw"]>[0];

// The profile of a figure the profile endpoint does not serve (delta, vanna, charm) is the latest value the heatmap holds for each strike.
const RECENT_COLUMNS = 12;

/** Both sides at once: per strike the latest call and put values, so the profile can draw a green and a red bar (an undefined side is not drawn). */
export function sideBarsFromHeatmaps(calls: HeatmapData | null, puts: HeatmapData | null): ProfileBar[] {
  const callByStrike = new Map((calls ? profileBarsFromHeatmap(calls) : []).map((bar) => [bar.strike, bar.net]));
  const putByStrike = new Map((puts ? profileBarsFromHeatmap(puts) : []).map((bar) => [bar.strike, bar.net]));
  const strikes = [...new Set([...callByStrike.keys(), ...putByStrike.keys()])].sort((a, b) => a - b);
  return strikes.map((strike) => {
    const call = callByStrike.get(strike);
    const put = putByStrike.get(strike);
    return { strike, net: (call ?? 0) + (put ?? 0), absolute: Math.abs(call ?? 0) + Math.abs(put ?? 0), call, put };
  });
}

/** One bar per strike: its latest stored value within the last few columns of the heatmap (a strike with none is left out). */
export function profileBarsFromHeatmap(data: HeatmapData): ProfileBar[] {
  const recent = data.columns.slice(-RECENT_COLUMNS);
  const bars: ProfileBar[] = [];
  data.strikes.forEach((strike, index) => {
    for (let offset = recent.length - 1; offset >= 0; offset -= 1) {
      const value = recent[offset].values[index];
      if (value !== null && value !== undefined && Number.isFinite(value)) {
        bars.push({ strike, net: value, absolute: Math.abs(value) });
        return;
      }
    }
  });
  return bars;
}

/**
 * Width, in CSS pixels, of the profile column for a pane of this width: a fifth of it between 150 and 320 px, and none on a pane too narrow to hold both the history
 * and the column (the chart then keeps its old full-width look).
 */
export function profileWidthCssPx(paneWidthCssPx: number): number {
  if (!(paneWidthCssPx >= COLUMN_MIN_PANE_WIDTH_CSS_PX)) return 0;
  return Math.min(COLUMN_MAX_CSS_PX, Math.max(COLUMN_MIN_CSS_PX, Math.round(paneWidthCssPx * COLUMN_WIDTH_FRACTION)));
}

function p98(values: number[]): number | null {
  const magnitudes = values.map(Math.abs).filter((value) => Number.isFinite(value) && value > 0).sort((a, b) => a - b);
  if (!magnitudes.length) return null;
  return magnitudes[Math.min(magnitudes.length - 1, Math.floor(LIVE_PROFILE_TOP_SHARE * magnitudes.length))];
}

/** The p98 of |value| (what the heatmap uses as the top of its scale): the strongest strikes read white instead of one outlier flattening the rest. */
export function profileScale(bars: ProfileBar[]): number | null {
  return p98(bars.map((bar) => bar.net));
}

function medianRowStep(ys: number[]): number | null {
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

type Placed<T> = { y: number; item: T };

function place<T extends { strike: number }>(source: LiveProfilePrimitive, items: T[], ratio: number, height: number): Placed<T>[] {
  const series = source.series;
  if (!series) return [];
  const placed: Placed<T>[] = [];
  for (const item of items) {
    const y = series.priceToCoordinate(item.strike);
    if (y === null || !Number.isFinite(y)) continue;
    const bitmapY = y * ratio;
    if (bitmapY < -50 * ratio || bitmapY > height + 50 * ratio) continue;
    placed.push({ y: bitmapY, item });
  }
  return placed;
}

class ProfileBarsRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly source: LiveProfilePrimitive) {}

  draw(target: DrawTarget): void {
    target.useBitmapCoordinateSpace((scope) => {
      const bars = this.source.bars;
      const width = scope.bitmapSize.width;
      const columnWidth = profileWidthCssPx(width / scope.horizontalPixelRatio) * scope.horizontalPixelRatio;
      if (!columnWidth || !bars.length) return;
      if (bars.some((bar) => bar.call !== undefined || bar.put !== undefined)) {
        this.drawSides(scope, bars, width, columnWidth);
        return;
      }
      // Length and colour share the same top (the p98) and a square-root scale: the strongest strikes fill the column and read white, and one dominant strike (SPX 2026-10-16:
      // 6.8B at the ABS strike against ~0.1B around it) no longer shrinks every other bar to a sliver. The order and the sign of every strike are unchanged.
      const largest = p98(bars.map((bar) => bar.absolute));
      const scale = profileScale(bars);
      if (largest === null || scale === null) return;
      const placed = place(this.source, bars, scope.verticalPixelRatio, scope.bitmapSize.height);
      if (!placed.length) return;
      const step = medianRowStep(placed.map((entry) => entry.y));
      const rowHeight = Math.max(MINIMUM_ROW_HEIGHT_BITMAP_PX, (step ?? scope.verticalPixelRatio * 6) * ROW_FILL_FRACTION);
      const lineX = width - columnWidth;
      const inset = BAR_INSET_CSS_PX * scope.horizontalPixelRatio;
      const maxBar = columnWidth - inset * 2;
      for (const { y, item } of placed) {
        const barWidth = Math.sqrt(Math.min(1, Math.abs(item.absolute) / largest)) * maxBar;
        if (barWidth < 1) continue;
        const rgb = rampRgb(Math.sign(item.net) * Math.sqrt(Math.min(1, Math.abs(item.net) / scale)) * scale, scale, this.source.palette);
        scope.context.fillStyle = `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
        scope.context.fillRect(lineX + inset, y - rowHeight / 2, barWidth, rowHeight);
      }
    });
  }

  /**
   * The sides view: per strike a green bar (call gamma) and a red bar (put gamma), the two halves of the row when both sides are on and the whole row when only one is. Length and colour share the
   * same top (the p98 of every |call| and |put| shown) and the same square-root scale as the single figure, so the call and the put of one strike compare directly.
   */
  drawSides(scope: Parameters<Parameters<DrawTarget["useBitmapCoordinateSpace"]>[0]>[0], bars: ProfileBar[], width: number, columnWidth: number): void {
    const magnitudes = bars.flatMap((bar) => [bar.call, bar.put]).filter((value): value is number => value !== undefined);
    const top = p98(magnitudes);
    if (top === null) return;
    const placed = place(this.source, bars, scope.verticalPixelRatio, scope.bitmapSize.height);
    if (!placed.length) return;
    const step = medianRowStep(placed.map((entry) => entry.y));
    const rowHeight = Math.max(MINIMUM_ROW_HEIGHT_BITMAP_PX, (step ?? scope.verticalPixelRatio * 6) * ROW_FILL_FRACTION);
    const both = bars.some((bar) => bar.call !== undefined) && bars.some((bar) => bar.put !== undefined);
    const half = Math.max(MINIMUM_ROW_HEIGHT_BITMAP_PX, rowHeight / 2 - 0.5 * scope.verticalPixelRatio);
    const lineX = width - columnWidth;
    const inset = BAR_INSET_CSS_PX * scope.horizontalPixelRatio;
    const maxBar = columnWidth - inset * 2;
    for (const { y, item } of placed) {
      for (const [value, upper] of [[item.call, true], [item.put, false]] as const) {
        if (value === undefined) continue;
        const barWidth = Math.sqrt(Math.min(1, Math.abs(value) / top)) * maxBar;
        if (barWidth < 1) continue;
        const rgb = rampRgb(Math.sign(value) * Math.sqrt(Math.min(1, Math.abs(value) / top)) * top, top, "gamma");
        scope.context.fillStyle = `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
        const height = both ? half : rowHeight;
        const offset = both ? (upper ? -half - 0.25 * scope.verticalPixelRatio : 0.25 * scope.verticalPixelRatio) : -rowHeight / 2;
        scope.context.fillRect(lineX + inset, y + offset, barWidth, height);
      }
    }
  }
}

class ProfileOverlayRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly source: LiveProfilePrimitive) {}

  draw(target: DrawTarget): void {
    target.useBitmapCoordinateSpace((scope) => {
      const { context, bitmapSize, horizontalPixelRatio: hr, verticalPixelRatio: vr } = scope;
      const columnWidth = profileWidthCssPx(bitmapSize.width / hr) * hr;
      if (!columnWidth) return;
      const lineX = bitmapSize.width - columnWidth;

      // The smile first, so the line and its label stay on top of the dots.
      const points = this.source.smile;
      if (points.length) {
        const ivs = points.map((point) => point.iv);
        const low = Math.min(...ivs);
        const high = Math.max(...ivs);
        const band = SMILE_BAND_CSS_PX * hr;
        const gap = SMILE_GAP_CSS_PX * hr;
        for (const { y, item } of place(this.source, points, vr, bitmapSize.height)) {
          const share = high > low ? (item.iv - low) / (high - low) : 0;
          const x = lineX - gap - (1 - share) * band;
          context.fillStyle = item.type === "call" ? CALL_DOT_COLOR : PUT_DOT_COLOR;
          context.beginPath();
          context.arc(x, y, SMILE_DOT_CSS_PX * hr, 0, Math.PI * 2);
          context.fill();
        }
        context.font = `${10.5 * hr}px ${FONT_FAMILY}`;
        context.textAlign = "right";
        context.textBaseline = "top";
        context.fillStyle = DIM_LABEL_COLOR;
        context.fillText("Smile · IV", lineX - 34 * hr, 8 * vr);
      }

      // The line of time.
      context.save();
      context.strokeStyle = AHORA_LINE_COLOR;
      context.lineWidth = Math.max(1, Math.round(hr));
      context.setLineDash([5 * hr, 4 * hr]);
      context.beginPath();
      context.moveTo(lineX, 0);
      context.lineTo(lineX, bitmapSize.height);
      context.stroke();
      context.restore();

      // AHORA, in a small rounded tag centred on the line at the top; the profile's own name at the foot of the column.
      context.font = `600 ${10.5 * hr}px ${FONT_FAMILY}`;
      context.textAlign = "center";
      context.textBaseline = "middle";
      const tagWidth = 50 * hr;
      const tagHeight = 16 * vr;
      context.fillStyle = "rgba(14, 17, 26, 0.92)";
      fillRounded(context, lineX - tagWidth / 2, 3 * vr, tagWidth, tagHeight, 5 * hr);
      context.fillStyle = LABEL_COLOR;
      context.fillText("AHORA", lineX, 3 * vr + tagHeight / 2 + 0.5 * vr);
      context.font = `500 ${10 * hr}px ${FONT_FAMILY}`;
      context.textAlign = "left";
      context.textBaseline = "bottom";
      context.fillStyle = DIM_LABEL_COLOR;
      context.fillText(this.source.label, lineX + BAR_INSET_CSS_PX * hr, bitmapSize.height - 5 * vr);
    });
  }
}

type RoundContext = {
  beginPath(): void;
  fill(): void;
  fillRect(x: number, y: number, width: number, height: number): void;
  roundRect?: (x: number, y: number, width: number, height: number, radius: number) => void;
};

/** A rounded rectangle where the canvas supports it, a plain one where it does not. */
function fillRounded(context: RoundContext, x: number, y: number, width: number, height: number, radius: number): void {
  if (typeof context.roundRect === "function") {
    context.beginPath();
    context.roundRect(x, y, width, height, radius);
    context.fill();
    return;
  }
  context.fillRect(x, y, width, height);
}

export class LiveProfilePrimitive implements ISeriesPrimitive<Time> {
  series: ISeriesApi<"Candlestick"> | null = null;
  bars: ProfileBar[] = [];
  smile: SmilePoint[] = [];
  palette: HeatmapPalette = "gamma";
  /** The caption at the foot of the column (the figure being shown). */
  label = "PERFIL EN VIVO";
  private requestUpdate: (() => void) | null = null;
  private readonly views: IPrimitivePaneView[];

  constructor() {
    const bars = new ProfileBarsRenderer(this);
    const overlay = new ProfileOverlayRenderer(this);
    this.views = [
      { zOrder: (): PrimitivePaneViewZOrder => "bottom", renderer: () => bars },
      { zOrder: (): PrimitivePaneViewZOrder => "top", renderer: () => overlay },
    ];
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
    return this.views;
  }

  setBars(bars: ProfileBar[], palette: HeatmapPalette = this.palette, label: string = this.label): void {
    this.bars = bars;
    this.palette = palette;
    this.label = label;
    this.requestUpdate?.();
  }

  setSmile(points: SmilePoint[]): void {
    this.smile = points;
    this.requestUpdate?.();
  }
}
