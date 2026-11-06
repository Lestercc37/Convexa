// VIX -> SPX / NDX translator (docs/estudio_vix_spx_2026-11-05.txt, part 1; Lester 2026-11-05 "construye la pastilla para SPX y NDX", VIX only, no VXN).
// From 37 sessions of minute prices (09-10 .. 11-05): how many index points moved per VIX point over 15-minute windows, the middle half of
// the cases (p25-p75), by the VIX level the window started at. SPX and VIX moved in opposite directions in 82-85 % of the windows.
// The bands are a study result, not a live calculation: re-measure them when the sample grows (memory vix_spx_translator_study_2026_11_05).

export type VixTarget = "SPX" | "NDX";

type Band = { maxVix: number; low: number; high: number };

// Points of the index per +1 VIX point (always opposite in sign): low..high = p25..p75 of |ratio|. Above 20 the 20-25 and >25 groups were
// alike (SPX 16-32 / 16-28) and >25 had only 13 windows, so they are one band (NDX 20+ = 75-164, from 64 windows).
const BANDS: Record<VixTarget, Band[]> = {
  SPX: [
    { maxVix: 17, low: 33, high: 60 },
    { maxVix: 20, low: 22, high: 44 },
    { maxVix: Infinity, low: 16, high: 32 },
  ],
  NDX: [
    { maxVix: 17, low: 151, high: 370 },
    { maxVix: 20, low: 112, high: 267 },
    { maxVix: Infinity, low: 75, high: 164 },
  ],
};

// ES reads SPX's band and NQ NDX's (index points and futures points move one to one); other symbols get no pill.
const TARGET_BY_SYMBOL: Record<string, VixTarget> = { SPX: "SPX", ES: "SPX", NDX: "NDX", NQ: "NDX" };

export function vixTargetFor(symbol: string): VixTarget | null {
  return TARGET_BY_SYMBOL[symbol.toUpperCase()] ?? null;
}

/** Index points per +1 VIX point at this VIX level, as a positive low..high (the index moves the other way). */
export function vixBand(target: VixTarget, vix: number): { low: number; high: number } {
  const band = BANDS[target].find((candidate) => vix < candidate.maxVix) ?? BANDS[target][BANDS[target].length - 1];
  return { low: band.low, high: band.high };
}

/**
 * The index move the VIX's change since the open translates to: the band of the VIX level at the open times the change, opposite in sign,
 * ordered from the smaller to the larger move ({from, to} both signed). Null when the VIX has not moved.
 */
export function translateVixChange(target: VixTarget, vixOpen: number, vixNow: number): { from: number; to: number } | null {
  const change = vixNow - vixOpen;
  if (!Number.isFinite(change) || change === 0) return null;
  const { low, high } = vixBand(target, vixOpen);
  return { from: -change * low, to: -change * high };
}

/** The VIX at today's open: the first stored reading at or after 09:31 ET today (VIX's own 09:30 print is often the previous close). */
export function vixOpenFrom(points: { timestamp: string; price: number }[], now: Date = new Date()): number | null {
  const today = etDate(now);
  for (const point of points) {
    const when = new Date(point.timestamp);
    if (etDate(when) === today && etMinutes(when) >= 9 * 60 + 31 && point.price > 0) return point.price;
  }
  return null;
}

function etDate(when: Date): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(when);
}

function etMinutes(when: Date): number {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(when);
  const value = (type: string) => Number(parts.find((part) => part.type === type)?.value ?? 0);
  return value("hour") * 60 + value("minute");
}
