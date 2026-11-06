import type { GammaView } from "@/lib/types";
import { columnP98, type HeatmapData } from "@/components/gamma-heatmap-primitive";

// "Growing" colour scale of the heatmap, a trial on ES 0DTE Volumen only (Lester 2026-11-05: "que los niveles se vayan [encendiendo] según el volumen va entrando").
// Every volume column is coloured against the 0DTE OI column of the same moment: gamma per contract is the same in both (same strike, same instant), so the
// ratio is the day's volume against open interest and it cannot blow up into the close the way 0DTE gamma does. Measured on SPX (docs/hm_growing_scale_check.py,
// p98 of the volume column / p98 of the OI column): 11-05 1.9x at 09:31, 3.4x at 10:00, 6.2x at 11:00, 12x at 14:00; 10-30 0.3x / 1.2x / 3.5x / 8.6x.
// A previous session's closing column as a fixed scale was rejected: 0DTE gamma explodes at 16:00, the next morning read 1-3 % of it.
// - TARGET_RATIO: volume at this multiple of the OI picture is full brightness (the column's own scale, exactly the default view);
// - MIN_BRIGHTNESS: the floor, so the whole map is there from 09:30, faint, instead of black.
export const GROWING_TARGET_RATIO = 10;
export const GROWING_MIN_BRIGHTNESS = 0.15;

export function usesGrowingScale(symbol: string, view: GammaView): boolean {
  return symbol.toUpperCase() === "ES" && view === "0dte_volume";
}

/** The scale of every volume column: max(own p98, TARGET x the OI column's p98), never above own p98 / MIN_BRIGHTNESS. No OI column at or before it: its own p98. */
export function growingScales(volume: HeatmapData, openInterest: HeatmapData): (number | null)[] {
  const oiColumns = openInterest.columns;
  let pointer = -1;
  return volume.columns.map((column) => {
    const own = columnP98(column.values);
    if (own === null) return null;
    while (pointer + 1 < oiColumns.length && oiColumns[pointer + 1].time <= column.time) pointer += 1;
    const oi = pointer >= 0 ? columnP98(oiColumns[pointer].values) : null;
    if (oi === null) return own;
    return Math.min(own / GROWING_MIN_BRIGHTNESS, Math.max(own, GROWING_TARGET_RATIO * oi));
  });
}
