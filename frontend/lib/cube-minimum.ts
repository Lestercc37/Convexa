// Minimum size of the alert cubes, per symbol (item 12b, Lester 2026-10-22): the API suggests one per symbol (about 40 cubes a session each, recomputed daily) and the viewer can type
// their own, which this browser remembers per symbol. Sizes are delta-equivalent contracts (contracts x |delta|).

/** What a symbol uses when the API has no suggestion for it (the old shared default). */
export const LEGACY_CUBE_MIN = 200;
export const CUBE_MIN_LIMIT = 1_000_000;
export const CUBE_MIN_STORAGE_KEY = "convexa.cube-min.v1";

export type CubeMinSource = "custom" | "auto" | "default";

/** A whole number from 1 to CUBE_MIN_LIMIT (thousands separators and spaces allowed), or null. */
export function parseCubeMin(input: string | number): number | null {
  const text = (typeof input === "number" ? String(input) : input).replace(/[\s,]/g, "");
  if (!/^\d+$/.test(text)) return null;
  const value = Number(text);
  return value >= 1 && value <= CUBE_MIN_LIMIT ? value : null;
}

export function effectiveCubeMin(
  symbol: string,
  custom: Record<string, number>,
  suggested: Record<string, number>,
): { value: number; source: CubeMinSource } {
  const key = symbol.toUpperCase();
  if (custom[key] !== undefined) return { value: custom[key], source: "custom" };
  if (suggested[key] !== undefined) return { value: suggested[key], source: "auto" };
  return { value: LEGACY_CUBE_MIN, source: "default" };
}

export function loadCubeMins(): Record<string, number> {
  try {
    const raw = window.localStorage.getItem(CUBE_MIN_STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return {};
    const out: Record<string, number> = {};
    for (const [symbol, value] of Object.entries(parsed as Record<string, unknown>)) {
      const valid = typeof value === "number" ? parseCubeMin(value) : null;
      if (valid !== null && /^[A-Z0-9.]{1,10}$/.test(symbol)) out[symbol] = valid;
    }
    return out;
  } catch {
    return {};
  }
}

export function saveCubeMins(values: Record<string, number>): void {
  try {
    window.localStorage.setItem(CUBE_MIN_STORAGE_KEY, JSON.stringify(values));
  } catch {
    // Storage blocked or full: the choice still holds for this visit.
  }
}
