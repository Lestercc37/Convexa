// The chart's price data is UTC epoch seconds (see candles.ts), but the
// regular session Convexa cares about (09:30-16:00) is defined in
// America/New_York wall-clock time -- this converts between the two
// without a date library, mirroring backend/domain/use_cases/market_hours.py's
// EASTERN_TIME/MARKET_OPEN_ET/MARKET_CLOSE_ET constants (the same session
// bounds, reused here rather than re-guessed).

export const EASTERN_TIME_ZONE = "America/New_York";
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

// Walks back to the most recent trading day (Mon-Fri) at or before
// `referenceMs`'s own Eastern calendar date -- Saturday rolls back 1 day
// to Friday, Sunday rolls back 2. Same "no exchange holiday calendar"
// limitation as regularSessionRange/market_hours.py's is_market_open,
// deliberately out of scope (a holiday would need one more day of
// rollback this doesn't attempt).
//
// Confirmed live, 2026-09 (a real Saturday): price-chart.tsx used to
// anchor the chart's left edge at `regularSessionRange(Date.now())`
// directly -- on a non-trading day that's the bounds of a session that
// never happened, computed purely from today's calendar date. Any
// stale-but-real price already in storage from a past weekday (the
// "latest" MarketPrice on file when nothing fresh has been written
// since) still passed the old isWithinRegularSession check, because
// that check only verified the timestamp was within *some* session --
// its own -- never that it matched today's or the most recent one. That
// stale point then violated lightweight-charts' strict ascending-order
// requirement once anchored against a `today` that came after it,
// crashing the whole dashboard. Anchoring against the most recent real
// session instead (Friday's, on a weekend) fixes both: the anchor
// itself is meaningful, and a genuinely-stale-relative-to-that-session
// point is what isWithinTheMostRecentSession below now actually rejects.
export function mostRecentSessionRange(referenceMs: number): SessionRange {
  const weekday = new Intl.DateTimeFormat("en-US", {
    timeZone: EASTERN_TIME_ZONE,
    weekday: "short",
  }).format(new Date(referenceMs));
  const rollBackDays = weekday === "Sun" ? 2 : weekday === "Sat" ? 1 : 0;
  const oneDayMs = 24 * 60 * 60 * 1000;
  return regularSessionRange(referenceMs - rollBackDays * oneDayMs);
}

// Mirrors backend/domain/use_cases/market_hours.py's is_market_open, but
// against the most recent real session as of `nowMs` (defaults to real
// "now"), not `referenceMs`'s own date -- see mostRecentSessionRange's
// own comment for exactly why that distinction matters. A tick genuinely
// from the most recent session (Friday's own price, checked over the
// weekend) is still valid; a stale tick from days before that session
// is not, even though it was a real, in-session price on its own day.
export function isWithinTheMostRecentSession(referenceMs: number, nowMs: number = Date.now()): boolean {
  const { openSeconds, closeSeconds } = mostRecentSessionRange(nowMs);
  const referenceSeconds = Math.floor(referenceMs / 1000);
  return referenceSeconds >= openSeconds && referenceSeconds < closeSeconds;
}
