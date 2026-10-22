// Exponential moving averages on the price chart (2026-10-22, Lester): any period, a colour per line, nothing fixed, so a new period never needs a deploy.

export type EmaConfig = { id: string; period: number; color: string; visible: boolean };

export const EMA_MIN_PERIOD = 1;
export const EMA_MAX_PERIOD = 1000;
export const EMA_MAX_LINES = 12;
export const EMA_STORAGE_KEY = "convexa.ema.v1";

// Twelve colours that stay apart on both a dark and a light chart; the next new line takes the first one not in use.
export const EMA_COLORS = [
  "#f5c542", "#4aa8ff", "#ff7a59", "#7ddc7a", "#c06cff", "#ff5fa2",
  "#2dd4bf", "#e8e8e8", "#ffa94d", "#8c9eff", "#b5e655", "#ff8787",
];

export type AddEmaResult = { emas: EmaConfig[]; error: null } | { emas: EmaConfig[]; error: "invalid" | "duplicate" | "max" };

/** An integer period within bounds, or null (blank, decimals, zero, negative, out of range). */
export function parseEmaPeriod(input: string | number): number | null {
  const text = typeof input === "number" ? String(input) : input.trim();
  if (!/^\d+$/.test(text)) return null;
  const period = Number(text);
  return period >= EMA_MIN_PERIOD && period <= EMA_MAX_PERIOD ? period : null;
}

export function nextEmaColor(emas: EmaConfig[]): string {
  const used = new Set(emas.map((ema) => ema.color.toLowerCase()));
  return EMA_COLORS.find((color) => !used.has(color)) ?? EMA_COLORS[emas.length % EMA_COLORS.length];
}

export function addEma(emas: EmaConfig[], input: string | number): AddEmaResult {
  const period = parseEmaPeriod(input);
  if (period === null) return { emas, error: "invalid" };
  if (emas.some((ema) => ema.period === period)) return { emas, error: "duplicate" };
  if (emas.length >= EMA_MAX_LINES) return { emas, error: "max" };
  const id = `ema-${period}`;
  return { emas: [...emas, { id, period, color: nextEmaColor(emas), visible: true }], error: null };
}

/**
 * The EMA of `closes` with `seed` (older closes, already in time order) feeding it first, so a long period starts warmed up from the previous sessions instead of at the first bar of
 * the day. Only the values of `closes` are returned (one per close): the seed never adds points to the chart. `warm` is false while seed + closes are fewer than `period` bars (the
 * line is still converging from its first value: shown with a hint, never hidden).
 */
export function emaValues(seed: number[], closes: number[], period: number): { values: number[]; warm: boolean } {
  const alpha = 2 / (period + 1);
  const all = seed.concat(closes);
  const values: number[] = [];
  let ema = all.length > 0 ? all[0] : 0;
  for (let index = 0; index < all.length; index += 1) {
    ema = index === 0 ? all[0] : alpha * all[index] + (1 - alpha) * ema;
    if (index >= seed.length) values.push(ema);
  }
  return { values, warm: all.length >= period };
}

// localStorage, per viewer only: a private window or blocked storage must leave the chart working with no EMAs.
function isEmaConfig(value: unknown): value is EmaConfig {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.id === "string" &&
    typeof candidate.period === "number" &&
    parseEmaPeriod(candidate.period) !== null &&
    typeof candidate.color === "string" &&
    /^#[0-9a-fA-F]{6}$/.test(candidate.color) &&
    typeof candidate.visible === "boolean"
  );
}

export function loadEmas(): EmaConfig[] {
  try {
    const raw = window.localStorage.getItem(EMA_STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const seen = new Set<number>();
    const emas: EmaConfig[] = [];
    for (const item of parsed) {
      if (!isEmaConfig(item) || seen.has(item.period) || emas.length >= EMA_MAX_LINES) continue;
      seen.add(item.period);
      emas.push({ id: item.id, period: item.period, color: item.color, visible: item.visible });
    }
    return emas;
  } catch {
    return [];
  }
}

export function saveEmas(emas: EmaConfig[]): void {
  try {
    window.localStorage.setItem(EMA_STORAGE_KEY, JSON.stringify(emas));
  } catch {
    // Storage blocked or full: the lines still work for this visit.
  }
}
