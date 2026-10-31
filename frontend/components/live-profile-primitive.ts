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
// The bars grow from the price axis (their flat side is stuck to the strikes, at the right edge) towards the candles, never closer to the AHORA line than a gutter.
// The Volatility Smile lives inside the same column (a toggle): one dot per strike for the IV of calls (green) and puts (red), IV growing to the right as in the rest of
// Convexa's smile, aligned with the price axis; hitSmile finds the dot under the mouse so the chart can say its strike, IV and expiration.

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

/** The IV range the dots are spread over: every point's. */
export function smileIvRange(points: SmilePoint[]): { low: number; high: number } {
  const ivs = points.map((point) => point.iv);
  return { low: Math.min(...ivs), high: Math.max(...ivs) };
}

/**
 * Horizontal centre, in CSS pixels from the left of the pane, of the dot of an IV: the lowest IV of the smile sits at the left end of the dot range (just past the gutter after the
 * AHORA line) and the highest at the right end, so IV grows to the right. A flat smile (one IV) is centred.
 */
export function smileDotX(iv: number, low: number, high: number, paneWidthCssPx: number, columnWidthCssPx: number): number {
  const lineX = paneWidthCssPx - columnWidthCssPx;
  const left = lineX + PROFILE_GUTTER_CSS_PX + SMILE_DOT_CSS_PX;
  const right = paneWidthCssPx - BAR_INSET_CSS_PX - SMILE_DOT_CSS_PX;
  const share = high > low ? Math.min(1, Math.max(0, (iv - low) / (high - low))) : 0.5;
  return left + share * Math.max(0, right - left);
}

// Below this pane width the column is hidden (the candles need the room). Was 520 until 2026-10-30: Lester's narrowed app windows lost the profile and
// it only came back near full monitor width; the v2 mockup keeps a 110 px column down to small screens.
const COLUMN_MIN_PANE_WIDTH_CSS_PX = 360;
// Under this pane width the column may be as narrow as COLUMN_MIN_NARROW_CSS_PX (the mockup's 110 px at <= 1100 px windows).
const COLUMN_NARROW_PANE_WIDTH_CSS_PX = 700;
const COLUMN_MIN_NARROW_CSS_PX = 110;
const COLUMN_WIDTH_FRACTION = 0.2;
const COLUMN_MIN_CSS_PX = 150;
const COLUMN_MAX_CSS_PX = 320;
const BAR_INSET_CSS_PX = 4;
// Free space between the AHORA line and the longest bar or the leftmost dot: the line and its tag never touch the histogram.
export const PROFILE_GUTTER_CSS_PX = 14;
const ROW_FILL_FRACTION = 0.78;
// A bar is never thicker than this: with strikes 60 to 100 px apart on screen (SPY, QQQ, IWM, NVDA) a full-row bar was a 45 px slab; SPX/NDX rows (about 24 px) stay as they were.
export const MAX_BAR_CSS_PX = 18;
const MINIMUM_ROW_HEIGHT_BITMAP_PX = 1;
const SMILE_DOT_CSS_PX = 2.6;
const SMILE_HIT_RADIUS_CSS_PX = 9;
export const LIVE_PROFILE_TOP_SHARE = 0.98;
export const AHORA_LINE_COLOR = "rgba(209, 212, 220, 0.78)";
const CALL_DOT_COLOR = "rgba(0, 220, 90, 0.92)";
const PUT_DOT_COLOR = "rgba(255, 92, 96, 0.92)";
const DOT_RING_COLOR = "rgba(8, 10, 16, 0.85)";
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
  const minimum = paneWidthCssPx < COLUMN_NARROW_PANE_WIDTH_CSS_PX ? COLUMN_MIN_NARROW_CSS_PX : COLUMN_MIN_CSS_PX;
  return Math.min(COLUMN_MAX_CSS_PX, Math.max(minimum, Math.round(paneWidthCssPx * COLUMN_WIDTH_FRACTION)));
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

/** Thickness in bitmap pixels of one profile bar: the strike spacing times the fill fraction, capped at MAX_BAR_CSS_PX. */
export function barThickness(strikeStepBitmapPx: number | null, verticalPixelRatio: number): number {
  const natural = (strikeStepBitmapPx ?? verticalPixelRatio * 6) * ROW_FILL_FRACTION;
  return Math.max(MINIMUM_ROW_HEIGHT_BITMAP_PX, Math.min(natural, MAX_BAR_CSS_PX * verticalPixelRatio));
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
      const rowHeight = barThickness(step, scope.verticalPixelRatio);
      const inset = BAR_INSET_CSS_PX * scope.horizontalPixelRatio;
      const maxBar = columnWidth - (PROFILE_GUTTER_CSS_PX * scope.horizontalPixelRatio + inset);
      const rightEdge = width - inset;
      for (const { y, item } of placed) {
        const barWidth = Math.sqrt(Math.min(1, Math.abs(item.absolute) / largest)) * maxBar;
        if (barWidth < 1) continue;
        const rgb = rampRgb(Math.sign(item.net) * Math.sqrt(Math.min(1, Math.abs(item.net) / scale)) * scale, scale, this.source.palette);
        scope.context.fillStyle = `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
        // flat side at the price axis, peak towards the candles
        scope.context.fillRect(rightEdge - barWidth, y - rowHeight / 2, barWidth, rowHeight);
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
    const rowHeight = barThickness(step, scope.verticalPixelRatio);
    const both = bars.some((bar) => bar.call !== undefined) && bars.some((bar) => bar.put !== undefined);
    const half = Math.max(MINIMUM_ROW_HEIGHT_BITMAP_PX, rowHeight / 2 - 0.5 * scope.verticalPixelRatio);
    const inset = BAR_INSET_CSS_PX * scope.horizontalPixelRatio;
    const maxBar = columnWidth - (PROFILE_GUTTER_CSS_PX * scope.horizontalPixelRatio + inset);
    const rightEdge = width - inset;
    for (const { y, item } of placed) {
      for (const [value, upper] of [[item.call, true], [item.put, false]] as const) {
        if (value === undefined) continue;
        const barWidth = Math.sqrt(Math.min(1, Math.abs(value) / top)) * maxBar;
        if (barWidth < 1) continue;
        const rgb = rampRgb(Math.sign(value) * Math.sqrt(Math.min(1, Math.abs(value) / top)) * top, top, "gamma");
        scope.context.fillStyle = `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
        const height = both ? half : rowHeight;
        const offset = both ? (upper ? -half - 0.25 * scope.verticalPixelRatio : 0.25 * scope.verticalPixelRatio) : -rowHeight / 2;
        scope.context.fillRect(rightEdge - barWidth, y + offset, barWidth, height);
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
      const paneWidthCss = bitmapSize.width / hr;
      this.source.paneWidthCss = paneWidthCss;
      const gutter = PROFILE_GUTTER_CSS_PX * hr;

      // The smile inside the column, over the bars (the line and its tag stay on top): a dot per strike and right, with a dark ring so it reads over any bar colour.
      const points = this.source.smile;
      if (points.length) {
        const { low, high } = smileIvRange(points);
        for (const { y, item } of place(this.source, points, vr, bitmapSize.height)) {
          const x = smileDotX(item.iv, low, high, paneWidthCss, columnWidth / hr) * hr;
          const color = item.type === "call" ? CALL_DOT_COLOR : PUT_DOT_COLOR;
          context.beginPath();
          context.arc(x, y, SMILE_DOT_CSS_PX * hr, 0, Math.PI * 2);
          context.fillStyle = color;
          context.fill();
          context.lineWidth = Math.max(1, hr * 0.9);
          context.strokeStyle = DOT_RING_COLOR;
          context.stroke();
        }
        context.font = `${10.5 * hr}px ${FONT_FAMILY}`;
        context.textAlign = "left";
        context.textBaseline = "top";
        context.fillStyle = DIM_LABEL_COLOR;
        context.fillText("Smile · IV", lineX + gutter, 26 * vr);
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

      // AHORA, in a small rounded tag on the history side of the line at the top (never over the histogram); the profile's own name at the foot of the column.
      context.font = `600 ${10.5 * hr}px ${FONT_FAMILY}`;
      context.textAlign = "center";
      context.textBaseline = "middle";
      const tagWidth = 50 * hr;
      const tagHeight = 16 * vr;
      context.fillStyle = "rgba(14, 17, 26, 0.92)";
      fillRounded(context, lineX - 6 * hr - tagWidth, 3 * vr, tagWidth, tagHeight, 5 * hr);
      context.fillStyle = LABEL_COLOR;
      context.fillText("AHORA", lineX - 6 * hr - tagWidth / 2, 3 * vr + tagHeight / 2 + 0.5 * vr);
      context.font = `500 ${10 * hr}px ${FONT_FAMILY}`;
      context.textAlign = "left";
      context.textBaseline = "bottom";
      context.fillStyle = DIM_LABEL_COLOR;
      context.fillText(this.source.label, lineX + gutter, bitmapSize.height - 5 * vr);
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
  /** Width of the pane in CSS pixels as of the last draw (the hit test places the dots with it). */
  paneWidthCss = 0;
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

  /**
   * The smile dot under a point of the pane (CSS pixels from its top left): the nearest one within a few pixels, or null. Same placement as the drawing (smileDotX and the
   * price coordinate of the strike), so what the mouse touches is what is drawn.
   */
  hitSmile(x: number, y: number, radius: number = SMILE_HIT_RADIUS_CSS_PX): SmilePoint | null {
    const series = this.series;
    if (!series || !this.smile.length || !(this.paneWidthCss > 0)) return null;
    const column = profileWidthCssPx(this.paneWidthCss);
    if (!column) return null;
    const { low, high } = smileIvRange(this.smile);
    let best: SmilePoint | null = null;
    let bestDistance = radius;
    for (const point of this.smile) {
      const py = series.priceToCoordinate(point.strike);
      if (py === null || !Number.isFinite(py)) continue;
      const distance = Math.hypot(smileDotX(point.iv, low, high, this.paneWidthCss, column) - x, py - y);
      if (distance <= bestDistance) {
        best = point;
        bestDistance = distance;
      }
    }
    return best;
  }
}
