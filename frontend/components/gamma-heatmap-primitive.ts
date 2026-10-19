import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";
import { timeToX } from "@/lib/chart-time";
import type { GammaHeatmapResponse } from "@/lib/types";

// Gamma heatmap: one rectangle per (time bucket, strike), drawn BEHIND the candles at the same coordinates they use
// (timeToX in lib/chart-time.ts, which places a time between bars / series.priceToCoordinate), so it follows zoom and scrolling with the chart instead of being a
// second chart kept in sync by hand. Same attachPrimitive pattern as the GEX histogram.
//
// Color = the stored per-strike figure of that bucket, scaled by the 98th percentile of |value| over the whole matrix (the API sends it as
// scale_p98): values at or above it get the top of the ramp, which is WHITE, so the strike(s) with the most exposure of the moment read as the
// brightest cells. Gamma and delta: positive = green ramp, negative = red ramp. Absolute gamma has no sign: one blue ramp. Vanna: purple (positive) and plum
// (negative). Charm: a muted orange (positive) and olive-ochre (negative), deliberately less bright than the gamma greens and reds.

export type HeatmapColumn = {
  /** Unix seconds of the column (floored to the minute so it lands on a candle). */
  time: number;
  /** One value per entry of `strikes`, same order; null = no stored value. In the sides view this is the CALL gamma of the cells. */
  values: (number | null)[];
  /** Sides view only: the PUT gamma (negative) of the same cells. A cell with both sides strong fades to white (sidesRgb). */
  puts?: (number | null)[];
};

export type HeatmapData = {
  strikes: number[];
  columns: HeatmapColumn[];
  scale: number | null;
  /** Which ramp draws the figure; gamma (green/red) when left out. */
  palette?: HeatmapPalette;
  /**
   * "column": every minute is coloured against ITS OWN distribution (its p98), so a column keeps the colours it was painted with for the rest of the session and the strongest strike of
   * each moment reads white, quiet or busy. "day": one scale for the whole matrix (`scale`, the API's p98 of the session; the old behaviour, and what a missing value means).
   */
  scaleMode?: "day" | "column";
};

export type HeatmapPalette = "gamma" | "unsigned" | "vanna" | "charm";

/**
 * The ramp of each stored figure: absolute gamma and the two-sided total have no sign (blue, never the green that means positive), vanna is purple, charm muted orange; call gamma
 * (stored positive) and put gamma (stored negative) use the gamma ramp, so calls draw green and puts red.
 */
export function paletteForMetric(metric: GammaHeatmapResponse["metric"]): HeatmapPalette {
  if (metric === "absolute_gamma" || metric === "total_gamma") return "unsigned";
  if (metric === "vanna") return "vanna";
  if (metric === "charm") return "charm";
  return "gamma";
}

/** The API response as the primitive wants it: columns floored to the response's own bucket (a minute for the cycle's rows, 10 or 30 s for the fast layer's) so they land where the candle's time axis puts them. */
export function heatmapFromResponse(response: GammaHeatmapResponse): HeatmapData {
  const bucketMs = Math.max(10, response.bucket_seconds || 60) * 1000;
  return {
    strikes: response.strikes,
    scale: response.scale_p98,
    palette: paletteForMetric(response.metric),
    columns: response.columns.map((column) => ({
      time: Math.floor(Date.parse(column.as_of) / bucketMs) * (bucketMs / 1000),
      values: column.values,
    })),
  };
}

/**
 * The session's earlier columns (the REST cycle's, one per minute) followed by the fast layer's: when the 10-second layer starts mid-session (or its worker restarts) the morning must not vanish.
 * `history` keeps only the columns BEFORE the first live column; strikes are the union of both, each column re-aligned to it. Without live columns the history is returned as is.
 */
export function prependHistory(history: HeatmapData | null, live: HeatmapData): HeatmapData {
  const firstLive = live.columns[0]?.time;
  if (history === null || firstLive === undefined) return live;
  const earlier = history.columns.filter((column) => column.time < firstLive);
  if (!earlier.length) return live;
  const strikes = [...new Set([...history.strikes, ...live.strikes])].sort((a, b) => a - b);
  const align = (source: HeatmapData) => {
    const index = new Map(source.strikes.map((strike, position) => [strike, position]));
    return (values: (number | null)[] | undefined): (number | null)[] | undefined =>
      values === undefined ? undefined : strikes.map((strike) => values[index.get(strike) ?? -1] ?? null);
  };
  const alignHistory = align(history);
  const alignLive = align(live);
  const columns: HeatmapColumn[] = [
    ...earlier.map((column) => ({ time: column.time, values: alignHistory(column.values)!, puts: alignHistory(column.puts) })),
    ...live.columns.map((column) => ({ time: column.time, values: alignLive(column.values)!, puts: alignLive(column.puts) })),
  ].map((column) => (column.puts === undefined ? { time: column.time, values: column.values } : column));
  const scales = [history.scale, live.scale].filter((value): value is number => value !== null);
  return { ...live, strikes, columns, scale: scales.length ? Math.max(...scales) : null };
}

type Stop = [number, number, number];

// Same ramps as the approved prototype (docs/prototipos/heatmap-flujo-alertas-2026-10-16.html).
export const HEATMAP_POSITIVE_STOPS: Stop[] = [[10, 61, 34], [8, 120, 60], [0, 168, 70], [0, 220, 90], [110, 255, 160], [255, 255, 255]];
export const HEATMAP_NEGATIVE_STOPS: Stop[] = [[74, 10, 13], [130, 8, 14], [190, 6, 12], [250, 0, 10], [255, 120, 110], [255, 255, 255]];
// A magnitude with no sign (absolute gamma): blue, so nobody reads it as 'bullish green' or 'bearish red'.
export const HEATMAP_UNSIGNED_STOPS: Stop[] = [[22, 36, 84], [34, 62, 150], [52, 100, 214], [96, 156, 255], [170, 206, 255], [255, 255, 255]];
// Vanna: purple when positive, plum (a hue step toward magenta) when negative, so the sign still reads without the green/red pair.
export const HEATMAP_VANNA_POSITIVE_STOPS: Stop[] = [[40, 22, 70], [78, 40, 140], [124, 70, 200], [168, 120, 235], [210, 180, 250], [255, 255, 255]];
export const HEATMAP_VANNA_NEGATIVE_STOPS: Stop[] = [[60, 18, 52], [110, 26, 92], [160, 40, 128], [205, 80, 170], [235, 150, 210], [255, 255, 255]];
// Charm: a muted orange when positive (softer than the gamma ramps on purpose), olive-ochre when negative.
export const HEATMAP_CHARM_POSITIVE_STOPS: Stop[] = [[58, 34, 14], [110, 62, 22], [158, 92, 32], [196, 124, 52], [226, 164, 102], [255, 255, 255]];
export const HEATMAP_CHARM_NEGATIVE_STOPS: Stop[] = [[44, 34, 18], [82, 66, 28], [120, 98, 38], [158, 134, 62], [200, 180, 110], [255, 255, 255]];

function rampStops(palette: HeatmapPalette, positive: boolean): Stop[] {
  switch (palette) {
    case "unsigned":
      return HEATMAP_UNSIGNED_STOPS;
    case "vanna":
      return positive ? HEATMAP_VANNA_POSITIVE_STOPS : HEATMAP_VANNA_NEGATIVE_STOPS;
    case "charm":
      return positive ? HEATMAP_CHARM_POSITIVE_STOPS : HEATMAP_CHARM_NEGATIVE_STOPS;
    default:
      return positive ? HEATMAP_POSITIVE_STOPS : HEATMAP_NEGATIVE_STOPS;
  }
}

// Below this share of the scale a cell is left empty: the background of a quiet strike is the chart, not a dark tile.
export const HEATMAP_MINIMUM_SHARE = 0.04;
export const HEATMAP_TOP_SHARE = 0.985;
export const HEATMAP_ALPHA = 0.62;
/** Blur of the smooth drawing, in CSS pixels (same value as the approved prototype). */
export const HEATMAP_BLUR_CSS_PX = 3;
// A grid with more rows than this is not drawn as one image (the sharp per-cell drawing is used instead).
const MAX_IMAGE_ROWS = 1500;
const ROW_FILL_FRACTION = 0.92;
// Strikes spaced wide apart ON SCREEN (SPY, QQQ, IWM at $1 and NVDA at $2.5 on a chart a few dollars tall: 60 to 100 px between strikes) must not become one tall solid block each: a band is never
// thicker than this, centred on its strike with soft edges, and the chart shows through between strikes. SPX and NDX (30 to 40 px between strikes) are below the limit and keep their approved look.
export const MAX_BAND_CSS_PX = 18;
// Bands start once adjacent strikes are more than this far apart on screen (SPX/NDX sit at 30 to 35 px, the ETFs at 60 to 100).
const BAND_ENGAGES_ABOVE_CSS_PX = 35;

/**
 * Thickness in CSS pixels of the band drawn for one strike given the on-screen distance between adjacent strikes: null while the strikes are close enough for the full-height rows (nothing
 * changes), `MAX_BAND_CSS_PX` once the gap is wider than BAND_ENGAGES_ABOVE_CSS_PX.
 */
export function bandHeightCssPx(strikeGapCssPx: number | null): number | null {
  if (strikeGapCssPx === null || !Number.isFinite(strikeGapCssPx)) return null;
  return strikeGapCssPx > BAND_ENGAGES_ABOVE_CSS_PX ? MAX_BAND_CSS_PX : null;
}
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

/** RGB of a cell, or null when it is too faint to draw. */
export function cellRgb(value: number | null, scale: number | null, palette: HeatmapPalette = "gamma"): [number, number, number] | null {
  if (value === null || scale === null || !(scale > 0)) return null;
  if (Math.abs(value) / scale < HEATMAP_MINIMUM_SHARE) return null;
  return rampRgb(value, scale, palette);
}

/** Same ramp as cellRgb but with no faint-cell floor: a bar of a profile is always drawn, however small. */
export function rampRgb(value: number, scale: number, palette: HeatmapPalette = "gamma"): [number, number, number] {
  const share = Math.abs(value) / scale;
  return rampColor(rampStops(palette, value >= 0), share >= HEATMAP_TOP_SHARE ? 1 : Math.min(1, share) * 0.96);
}

// The scale of every column: its own p98 (over calls and puts together in the sides view) in "column" mode, the whole matrix's scale otherwise. Cached per matrix.
const COLUMN_SCALES = new WeakMap<HeatmapData, (number | null)[]>();

export function columnScales(data: HeatmapData): (number | null)[] {
  const cached = COLUMN_SCALES.get(data);
  if (cached) return cached;
  const scales = data.columns.map((column) => {
    if (data.scaleMode !== "column") return data.scale;
    const magnitudes = [...column.values, ...(column.puts ?? [])]
      .filter((value): value is number => value !== null && value !== undefined && Number.isFinite(value))
      .map(Math.abs)
      .sort((a, b) => a - b);
    if (!magnitudes.length) return null;
    const p98 = magnitudes[Math.min(magnitudes.length - 1, Math.floor(0.98 * magnitudes.length))];
    return p98 > 0 ? p98 : null;
  });
  COLUMN_SCALES.set(data, scales);
  return scales;
}

// The weaker side of a cell reaches this share of the scale and the cell is fully white.
export const SIDES_WHITE_AT = 0.6;

/**
 * Colour of a cell in the sides view. One side alone: its own ramp (calls green, puts red, each fading to white with its intensity). Both sides: the STRONGER side's colour
 * whitened by how strong the weaker one is, white once the weaker side reaches SIDES_WHITE_AT of the scale, so a strike strong on both sides reads white (never amber, which
 * would pass for the ABS level) and a strike where one side dominates keeps its colour. Null when both are too faint to draw.
 */
export function sidesRgb(call: number | null, put: number | null, scale: number | null): [number, number, number] | null {
  const callRgb = cellRgb(call, scale, "gamma");
  const putRgb = cellRgb(put, scale, "gamma");
  if (callRgb === null || putRgb === null || call === null || put === null || scale === null) return callRgb ?? putRgb;
  const callShare = Math.min(1, Math.abs(call) / scale);
  const putShare = Math.min(1, Math.abs(put) / scale);
  const base = callShare >= putShare ? callRgb : putRgb;
  const white = Math.min(1, Math.min(callShare, putShare) / SIDES_WHITE_AT);
  return [
    Math.round(base[0] + (255 - base[0]) * white),
    Math.round(base[1] + (255 - base[1]) * white),
    Math.round(base[2] + (255 - base[2]) * white),
  ];
}

/** RGB of one cell of a column: the sides colour when the column carries puts, the single-figure ramp otherwise. */
function columnCellRgb(data: HeatmapData, column: HeatmapColumn, columnIndex: number, index: number): [number, number, number] | null {
  const scale = columnScales(data)[columnIndex] ?? null;
  if (column.puts !== undefined) return sidesRgb(column.values[index] ?? null, column.puts[index] ?? null, scale);
  return cellRgb(column.values[index] ?? null, scale, data.palette);
}

/**
 * Calls and puts of two heatmap responses as ONE matrix for the sides view: the union of their strikes and times, one shared scale (the larger p98) so the call and the put of a
 * cell compare directly. With only one side given that side is returned as is (its own ramp).
 */
export function mergeSides(calls: HeatmapData | null, puts: HeatmapData | null): HeatmapData | null {
  if (calls === null || puts === null) return calls ?? puts;
  const strikes = [...new Set([...calls.strikes, ...puts.strikes])].sort((a, b) => a - b);
  const times = [...new Set([...calls.columns.map((column) => column.time), ...puts.columns.map((column) => column.time)])].sort((a, b) => a - b);
  const align = (data: HeatmapData) => {
    const byTime = new Map(data.columns.map((column) => [column.time, column.values]));
    const strikeIndex = new Map(data.strikes.map((strike, index) => [strike, index]));
    return (time: number): (number | null)[] => {
      const values = byTime.get(time);
      return strikes.map((strike) => {
        const index = strikeIndex.get(strike);
        return values === undefined || index === undefined ? null : (values[index] ?? null);
      });
    };
  };
  const callsAt = align(calls);
  const putsAt = align(puts);
  const scales = [calls.scale, puts.scale].filter((value): value is number => value !== null);
  return {
    strikes,
    scaleMode: calls.scaleMode ?? puts.scaleMode,
    scale: scales.length ? Math.max(...scales) : null,
    columns: times.map((time) => ({ time, values: callsAt(time), puts: putsAt(time) })),
  };
}

/** CSS color of a cell, or null when it is too faint to draw. */
export function cellColor(value: number | null, scale: number | null, palette: HeatmapPalette = "gamma"): string | null {
  const rgb = cellRgb(value, scale, palette);
  if (rgb === null) return null;
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${HEATMAP_ALPHA})`;
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

function gcd(a: number, b: number): number {
  return b === 0 ? a : gcd(b, a % b);
}

/**
 * Row step of the picture: the largest step every gap between neighbouring strikes is a whole multiple of (the greatest common divisor of the gaps, to
 * the cent), so strikes of different spacing (NDX: 5 near the money, 10 further out, 25 in the wings) all land on a row. Coarsened when that would take
 * more than MAX_IMAGE_ROWS rows. Null with fewer than two distinct strikes.
 */
export function strikeGridStep(strikes: number[]): number | null {
  const sorted = [...new Set(strikes)].sort((a, b) => a - b);
  if (sorted.length < 2) return null;
  let divisor = 0;
  for (let index = 1; index < sorted.length; index += 1) {
    divisor = gcd(Math.round((sorted[index] - sorted[index - 1]) * 100), divisor);
  }
  if (divisor <= 0) return null;
  const range = sorted[sorted.length - 1] - sorted[0];
  const step = divisor / 100;
  return range / step + 1 <= MAX_IMAGE_ROWS ? step : range / (MAX_IMAGE_ROWS - 1);
}

export type HeatmapImage = {
  /** One pixel per column. */
  width: number;
  /** One pixel per row of the regular price grid (strikes of any spacing), the highest price first (row 0). */
  height: number;
  pixels: Uint8ClampedArray<ArrayBuffer>;
  lowStrike: number;
  highStrike: number;
};

/**
 * The matrix as a one-pixel-per-row-and-column RGBA image (what the smooth drawing scales up and blurs). Every row of the regular price grid takes the
 * value of its NEAREST strike, so strikes spaced 5, 10 or 25 apart paint a continuous map instead of blank rows between them; a row farther than
 * 1.5 median gaps from every strike (a hole in the strikes) stays empty. Null without columns or with fewer than two strikes.
 */
export function heatmapImage(data: HeatmapData): HeatmapImage | null {
  if (!data.columns.length) return null;
  const step = strikeGridStep(data.strikes);
  if (step === null) return null;
  const order = data.strikes.map((_, index) => index).sort((a, b) => data.strikes[a] - data.strikes[b]);
  const sortedStrikes = order.map((index) => data.strikes[index]);
  const lowStrike = sortedStrikes[0];
  const highStrike = sortedStrikes[sortedStrikes.length - 1];
  const height = Math.round((highStrike - lowStrike) / step) + 1;
  const width = data.columns.length;
  const medianGap = medianStep(sortedStrikes) ?? step;
  const reach = 1.5 * medianGap;
  // Nearest strike of every grid row (walking both sorted lists once).
  const rowStrike: number[] = new Array(height).fill(-1);
  let pointer = 0;
  for (let row = 0; row < height; row += 1) {
    const price = lowStrike + row * step;
    while (pointer + 1 < sortedStrikes.length && Math.abs(sortedStrikes[pointer + 1] - price) <= Math.abs(sortedStrikes[pointer] - price)) pointer += 1;
    if (Math.abs(sortedStrikes[pointer] - price) <= reach) rowStrike[row] = order[pointer];
  }
  const pixels = new Uint8ClampedArray(new ArrayBuffer(width * height * 4));
  const alpha = Math.round(HEATMAP_ALPHA * 255);
  data.columns.forEach((column, columnIndex) => {
    for (let row = 0; row < height; row += 1) {
      const strikeIndex = rowStrike[row];
      if (strikeIndex < 0) continue;
      const rgb = columnCellRgb(data, column, columnIndex, strikeIndex);
      if (rgb === null) continue;
      const offset = ((height - 1 - row) * width + columnIndex) * 4;
      pixels[offset] = rgb[0];
      pixels[offset + 1] = rgb[1];
      pixels[offset + 2] = rgb[2];
      pixels[offset + 3] = alpha;
    }
  });
  return { width, height, pixels, lowStrike, highStrike };
}

type CachedImage = { source: HeatmapData; image: HeatmapImage; canvas: HTMLCanvasElement };

class GammaHeatmapRenderer implements IPrimitivePaneRenderer {
  private cached: CachedImage | null = null;
  private scratch: HTMLCanvasElement | null = null;

  constructor(private readonly source: GammaHeatmapPrimitive) {}

  /** The one-pixel-per-cell image of the current data, rebuilt only when the data changes; null when it cannot be built here. */
  private imageFor(data: HeatmapData): CachedImage | null {
    if (this.cached && this.cached.source === data) return this.cached;
    const image = heatmapImage(data);
    if (!image || typeof document === "undefined" || typeof ImageData === "undefined") return null;
    const canvas = document.createElement("canvas");
    canvas.width = image.width;
    canvas.height = image.height;
    const context = canvas.getContext("2d");
    if (!context) return null;
    context.putImageData(new ImageData(image.pixels, image.width, image.height), 0, 0);
    this.cached = { source: data, image, canvas };
    return this.cached;
  }

  /**
   * Smooth drawing, like the approved prototype: the matrix is one pixel per cell, each column is stretched to its width and the full height of the
   * strike range with interpolation, and the result is blurred. Only the visible part is composed, so zooming in never builds a huge canvas.
   * Returns false when it cannot be used (no canvas, strikes not on one grid, prices off screen): the caller then draws the sharp cells.
   */
  private drawSmooth(scope: Parameters<Parameters<DrawTarget["useBitmapCoordinateSpace"]>[0]>[0]): boolean {
    const { series, chart, data } = this.source;
    if (!series || !chart || !data) return false;
    const cached = this.imageFor(data);
    if (!cached) return false;
    const { image, canvas } = cached;
    const yHigh = series.priceToCoordinate(image.highStrike);
    const yLow = series.priceToCoordinate(image.lowStrike);
    if (yHigh === null || yLow === null || !Number.isFinite(yHigh) || !Number.isFinite(yLow) || image.height < 2) return false;
    const hpr = scope.horizontalPixelRatio;
    const vpr = scope.verticalPixelRatio;
    const halfRow = (Math.abs(yLow - yHigh) / (image.height - 1) / 2) * vpr;
    const top = Math.min(yHigh, yLow) * vpr - halfRow;
    const bottom = Math.max(yHigh, yLow) * vpr + halfRow;

    const timeScale = chart.timeScale();
    const xs = data.columns.map((column) => timeToX(timeScale, series, column.time));
    const placed: { index: number; left: number; width: number }[] = [];
    let lastWidth = 0;
    data.columns.forEach((_, index) => {
      const x = xs[index];
      if (x === null || !Number.isFinite(x)) return;
      const next = xs[index + 1];
      const width =
        next !== null && next !== undefined && Number.isFinite(next) && next > x ? (next - x) * hpr : lastWidth || 8 * hpr;
      lastWidth = width;
      placed.push({ index, left: x * hpr, width });
    });
    if (!placed.length) return false;

    // Compose only what is on screen (plus a margin so the blur has real pixels at the edges).
    const margin = (HEATMAP_BLUR_CSS_PX * 3 + 2) * hpr;
    const totalLeft = placed[0].left;
    const totalRight = placed[placed.length - 1].left + placed[placed.length - 1].width;
    const left = Math.max(totalLeft, -margin);
    const right = Math.min(totalRight, scope.bitmapSize.width + margin);
    const visibleTop = Math.max(top, -margin);
    const visibleBottom = Math.min(bottom, scope.bitmapSize.height + margin);
    if (right <= left || visibleBottom <= visibleTop) return true; // everything off screen: nothing to draw
    const width = Math.ceil(right - left);
    const height = Math.ceil(visibleBottom - visibleTop);

    const strikeYs = data.strikes
      .map((strike) => series.priceToCoordinate(strike))
      .filter((y): y is NonNullable<typeof y> => y !== null && Number.isFinite(y));
    const bandCss = bandHeightCssPx(medianStep(strikeYs));

    const scratch = this.scratch ?? document.createElement("canvas");
    this.scratch = scratch;
    if (scratch.width !== width || scratch.height !== height) {
      scratch.width = width;
      scratch.height = height;
    }
    const scratchContext = scratch.getContext("2d");
    if (!scratchContext) return false;
    scratchContext.clearRect(0, 0, width, height);
    scratchContext.imageSmoothingEnabled = true;
    scratchContext.imageSmoothingQuality = "high";
    for (const column of placed) {
      if (column.left + column.width < left || column.left > right) continue;
      scratchContext.drawImage(canvas, column.index, 0, 1, image.height, column.left - left, top - visibleTop, column.width + 0.75, bottom - top);
    }

    // Far-apart strikes: keep only each strike's own band (the stretched image between them was interpolation, not data).
    if (bandCss !== null) this.maskBands(scratch, strikeYs.map((y) => y * vpr - visibleTop), bandCss * vpr, width, height);

    const context = scope.context;
    context.save();
    context.beginPath();
    context.rect(left, visibleTop, width, height);
    context.clip();
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = "high";
    context.filter = `blur(${HEATMAP_BLUR_CSS_PX * hpr}px)`;
    context.drawImage(scratch, left, visibleTop);
    context.filter = "none";
    context.restore();
    return true;
  }

  private mask: HTMLCanvasElement | null = null;

  /**
   * Keeps only a band of `bandHeight` bitmap pixels around each strike (soft top and bottom edges) and clears the rest of the composed image. Used when the strikes are far apart on screen, so each
   * strike reads as its own band of gamma instead of the whole span washing into one smear. `ys` are the strikes' vertical centres in the scratch canvas' coordinates.
   */
  private maskBands(scratch: HTMLCanvasElement, ys: number[], bandHeight: number, width: number, height: number): boolean {
    const mask = this.mask ?? document.createElement("canvas");
    this.mask = mask;
    if (mask.width !== width || mask.height !== height) {
      mask.width = width;
      mask.height = height;
    }
    const maskContext = mask.getContext("2d");
    const scratchContext = scratch.getContext("2d");
    if (!maskContext || !scratchContext) return false;
    maskContext.clearRect(0, 0, width, height);
    for (const y of ys) {
      const top = y - bandHeight / 2;
      if (top > height || top + bandHeight < 0) continue;
      const gradient = maskContext.createLinearGradient(0, top, 0, top + bandHeight);
      gradient.addColorStop(0, "rgba(0, 0, 0, 0)");
      gradient.addColorStop(0.22, "rgba(0, 0, 0, 1)");
      gradient.addColorStop(0.78, "rgba(0, 0, 0, 1)");
      gradient.addColorStop(1, "rgba(0, 0, 0, 0)");
      maskContext.fillStyle = gradient;
      maskContext.fillRect(0, top, width, bandHeight);
    }
    scratchContext.globalCompositeOperation = "destination-in";
    scratchContext.drawImage(mask, 0, 0);
    scratchContext.globalCompositeOperation = "source-over";
    return true;
  }

  draw(target: DrawTarget): void {
    target.useBitmapCoordinateSpace((scope) => {
      const { series, chart, data } = this.source;
      if (!series || !chart || !data || !data.columns.length || !data.strikes.length) return;
      if (this.source.smooth && this.drawSmooth(scope)) return;
      const timeScale = chart.timeScale();

      const rows: { index: number; y: number }[] = [];
      data.strikes.forEach((strike, index) => {
        const y = series.priceToCoordinate(strike);
        if (y !== null && Number.isFinite(y)) rows.push({ index, y: y * scope.verticalPixelRatio });
      });
      if (!rows.length) return;
      const step = medianStep(rows.map((row) => row.y));
      const sharpBand = bandHeightCssPx(step === null ? null : step / scope.verticalPixelRatio);
      const rowHeight = Math.max(
        MINIMUM_ROW_HEIGHT_BITMAP_PX,
        sharpBand !== null ? sharpBand * scope.verticalPixelRatio : (step ?? scope.verticalPixelRatio * 6) * ROW_FILL_FRACTION,
      );

      const xs = data.columns.map((column) => timeToX(timeScale, series, column.time));
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
          const rgb = columnCellRgb(data, column, position, row.index);
          if (rgb === null) continue;
          scope.context.fillStyle = `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${HEATMAP_ALPHA})`;
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
  /** Smooth drawing (scaled and blurred, like the prototype) instead of one sharp rectangle per cell. The chart turns it on; it falls back to the sharp cells when it cannot run. */
  readonly smooth: boolean;

  constructor(options: { smooth?: boolean } = {}) {
    this.smooth = options.smooth ?? false;
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
