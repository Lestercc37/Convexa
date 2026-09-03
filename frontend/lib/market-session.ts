// The chart's price data is UTC epoch seconds (see candles.ts), but the
// regular session Convexa cares about (09:30-16:00) is defined in
// America/New_York wall-clock time -- this converts between the two
// without a date library, mirroring backend/domain/use_cases/market_hours.py's
// EASTERN_TIME/MARKET_OPEN_ET/MARKET_CLOSE_ET constants (the same session
// bounds, reused here rather than re-guessed).

const EASTERN_TIME_ZONE = "America/New_York";
const MARKET_OPEN_HOUR = 9;
const MARKET_OPEN_MINUTE = 30;
const MARKET_CLOSE_HOUR = 16;
const MARKET_CLOSE_MINUTE = 0;

function datePart(parts: Intl.DateTimeFormatPart[], type: Intl.DateTimeFormatPartTypes): number {
  const value = parts.find((part) => part.type === type)?.value;
  return value ? Number(value) : NaN;
}

function easternCalendarDate(referenceMs: number): { year: number; month: number; day: number } {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: EASTERN_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(referenceMs));
  return { year: datePart(parts, "year"), month: datePart(parts, "month"), day: datePart(parts, "day") };
}

// Resolves a wall-clock hour:minute on a given Eastern calendar date to
// the UTC instant it actually represents -- one guess-and-correct pass
// against the real EST/EDT offset in effect on that date. Reliable
// except on the DST transition day itself (same documented-limitation
// convention as market_hours.py's own holiday-calendar gap: known,
// deliberately out of scope).
function easternWallTimeToUtcMs(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
): number {
  const guessUtcMs = Date.UTC(year, month - 1, day, hour, minute, 0);
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: EASTERN_TIME_ZONE,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(new Date(guessUtcMs));
  const observedAsUtcMs = Date.UTC(
    datePart(parts, "year"),
    datePart(parts, "month") - 1,
    datePart(parts, "day"),
    datePart(parts, "hour"),
    datePart(parts, "minute"),
    datePart(parts, "second"),
  );
  const drift = observedAsUtcMs - guessUtcMs;
  return guessUtcMs - drift;
}

export type SessionRange = { openSeconds: number; closeSeconds: number };

// The current regular session's 09:30-16:00 ET bounds, in UTC epoch
// seconds -- the same reference frame as MinuteCandle.time -- for
// whatever Eastern calendar date `referenceMs` falls on.
export function regularSessionRange(referenceMs: number): SessionRange {
  const { year, month, day } = easternCalendarDate(referenceMs);
  const openMs = easternWallTimeToUtcMs(year, month, day, MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE);
  const closeMs = easternWallTimeToUtcMs(year, month, day, MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE);
  return { openSeconds: Math.floor(openMs / 1000), closeSeconds: Math.floor(closeMs / 1000) };
}

// Mirrors backend/domain/use_cases/market_hours.py's is_market_open:
// weekday (Mon-Fri) and the half-open [09:30, 16:00) ET interval -- same
// known, deliberate limitation (no exchange holiday calendar). Used as a
// second, defense-in-depth check on the frontend: the backend stream
// gate (StreamUnderlyingPriceUseCase) stops *new* extended-hours ticks
// from ever being stored, but a tick written before that gate existed
// can still be the "latest" MarketPrice the API returns until the next
// in-session write -- this keeps dashboard.tsx from ever plotting one
// regardless of what storage currently holds.
export function isWithinRegularSession(referenceMs: number): boolean {
  const weekday = new Intl.DateTimeFormat("en-US", {
    timeZone: EASTERN_TIME_ZONE,
    weekday: "short",
  }).format(new Date(referenceMs));
  if (weekday === "Sat" || weekday === "Sun") return false;
  const { openSeconds, closeSeconds } = regularSessionRange(referenceMs);
  const referenceSeconds = Math.floor(referenceMs / 1000);
  return referenceSeconds >= openSeconds && referenceSeconds < closeSeconds;
}
