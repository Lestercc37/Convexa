// The heatmap painted to the close, a trial on ES only (Lester 2026-11-06: "pintar toda la pantalla a las 9:30, hasta las 16:00"). The time axis gets empty
// slots up to 16:00 (on a separate, invisible series, so the live candle updates are untouched) and the heatmap stretches its latest column from the last
// candle to the close in a softer tone (HEATMAP_PROJECTION_ALPHA in gamma-heatmap-primitive.ts), re-drawn on every poll as volume comes in.
export function projectsSession(symbol: string): boolean {
  return symbol.toUpperCase() === "ES";
}

/** Every candle slot of the session from the open to the last one before the close, bucketed like the candles (floor(time / step) * step). */
export function sessionSlots(openSeconds: number, closeSeconds: number, stepSeconds: number): number[] {
  const slots: number[] = [];
  for (let time = Math.floor(openSeconds / stepSeconds) * stepSeconds; time < closeSeconds; time += stepSeconds) {
    if (!slots.length || slots[slots.length - 1] !== time) slots.push(time);
  }
  return slots;
}
