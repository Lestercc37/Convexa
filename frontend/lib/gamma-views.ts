import type { GammaView } from "@/lib/types";

// The dashboard's gamma tabs (Lester 2026-10-26): one listed expiration each, plus the combined 0-2 DTE view. Every option-based figure on screen
// (levels, regime, profile, heatmap, level history) follows the selected tab; VWAP and Anchored VWAP come from the underlying's price and volume and do not.
export const GAMMA_VIEW_TABS: readonly { view: GammaView; label: string }[] = [
  { view: "0dte", label: "0DTE" },
  { view: "1dte", label: "1DTE" },
  { view: "2dte", label: "2DTE" },
  { view: "tactical", label: "0-2 DTE" },
];

export const DEFAULT_GAMMA_VIEW: GammaView = "0dte";

const EXPIRATION_POSITION: Partial<Record<GammaView, number>> = { "0dte": 0, "1dte": 1, "2dte": 2, "0dte_volume": 0 };

// "OI | Volumen" (2026-10-26): the open interest (default) or the contracts traded today as the weight of every contract. Only the 0DTE tab has a
// volume twin on the backend for now ("0dte_volume"); on the other tabs the volume button is disabled and the open interest is used.
export type GammaWeight = "oi" | "volume";
const VOLUME_VIEW_BY_TAB: Partial<Record<GammaView, GammaView>> = { "0dte": "0dte_volume" };
const WEIGHT_STORAGE_KEY = "convexa.gamma-weight.v1";

export function supportsVolumeWeight(tab: GammaView): boolean {
  return VOLUME_VIEW_BY_TAB[tab] !== undefined;
}

/** The view every request asks for: the tab itself, or its volume-weighted twin when "Volumen" is on and the tab has one. */
export function effectiveGammaView(tab: GammaView, weight: GammaWeight): GammaView {
  return weight === "volume" ? (VOLUME_VIEW_BY_TAB[tab] ?? tab) : tab;
}

export function readStoredGammaWeight(): GammaWeight {
  try {
    return window.localStorage.getItem(WEIGHT_STORAGE_KEY) === "volume" ? "volume" : "oi";
  } catch {
    return "oi";
  }
}

export function storeGammaWeight(weight: GammaWeight): void {
  try {
    window.localStorage.setItem(WEIGHT_STORAGE_KEY, weight);
  } catch {
    // Not remembered, still applied.
  }
}

// ES / NQ read the levels of SPX / NDX (shifted by the typed opening price), so their tabs show the index's expirations.
export const GAMMA_SOURCE_SYMBOL_BY_FUTURE: Record<string, string> = { ES: "SPX", NQ: "NDX" };

const STORAGE_KEY = "convexa.gamma-view.v1";

export function isExpirationView(view: GammaView): boolean {
  return EXPIRATION_POSITION[view] !== undefined;
}

/** Today's date in New York as YYYY-MM-DD (the market's day, not the browser's). */
export function todayInNewYork(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

/** The expiration a per-expiration tab reads, by the backend's own rule (gamma.py listed_expiration_for_view): the n-th LISTED expiration from today on. */
export function listedExpirationForView(expirations: readonly string[], view: GammaView, today: string): string | null {
  const position = EXPIRATION_POSITION[view];
  if (position === undefined) return null;
  const listed = [...new Set(expirations.filter((expiration) => expiration >= today))].sort();
  return listed[position] ?? null;
}

export function readStoredGammaView(): GammaView | null {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return GAMMA_VIEW_TABS.some((tab) => tab.view === stored) ? (stored as GammaView) : null;
  } catch {
    return null;
  }
}

export function storeGammaView(view: GammaView): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, view);
  } catch {
    // Private window or blocked storage: the tab still works, it is just not remembered.
  }
}
