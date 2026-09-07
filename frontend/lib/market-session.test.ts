import { describe, expect, it } from "vitest";
import { isWithinTheMostRecentSession, mostRecentSessionRange, regularSessionRange } from "./market-session";

describe("regularSessionRange", () => {
  it("resolves 09:30-16:00 ET during EDT (summer)", () => {
    const referenceMs = Date.UTC(2026, 8, 3, 18, 0, 0); // 2026-09-03 14:00 ET
    const { openSeconds, closeSeconds } = regularSessionRange(referenceMs);

    expect(new Date(openSeconds * 1000).toISOString()).toBe("2026-09-03T13:30:00.000Z");
    expect(new Date(closeSeconds * 1000).toISOString()).toBe("2026-09-03T20:00:00.000Z");
  });

  it("resolves 09:30-16:00 ET during EST (winter)", () => {
    const referenceMs = Date.UTC(2026, 0, 15, 18, 0, 0); // 2026-01-15 13:00 ET
    const { openSeconds, closeSeconds } = regularSessionRange(referenceMs);

    expect(new Date(openSeconds * 1000).toISOString()).toBe("2026-01-15T14:30:00.000Z");
    expect(new Date(closeSeconds * 1000).toISOString()).toBe("2026-01-15T21:00:00.000Z");
  });

  it("uses the Eastern calendar date, not the UTC one, near midnight UTC", () => {
    // 2026-09-04T02:00:00Z is still 2026-09-03 22:00 ET (before midnight
    // Eastern) -- the session must resolve to 09-03's bounds, not 09-04's.
    const referenceMs = Date.UTC(2026, 8, 4, 2, 0, 0);
    const { openSeconds } = regularSessionRange(referenceMs);

    expect(new Date(openSeconds * 1000).toISOString()).toBe("2026-09-03T13:30:00.000Z");
  });
});

describe("mostRecentSessionRange", () => {
  // 2026-09-03/04 are Thu/Fri, 09-05 Sat, 09-06 Sun, 09-07 Mon.
  it("resolves to the same day's session on a weekday", () => {
    const referenceMs = Date.UTC(2026, 8, 3, 18, 0, 0); // Thu 14:00 ET
    const result = mostRecentSessionRange(referenceMs);

    expect(result).toEqual(regularSessionRange(referenceMs));
  });

  it("rolls back one day, to Friday, when referenceMs is a Saturday", () => {
    const saturday = Date.UTC(2026, 8, 5, 14, 0, 0); // Sat 10:00 ET
    const friday = Date.UTC(2026, 8, 4, 14, 0, 0); // Fri 10:00 ET

    expect(mostRecentSessionRange(saturday)).toEqual(regularSessionRange(friday));
  });

  it("rolls back two days, to Friday, when referenceMs is a Sunday", () => {
    const sunday = Date.UTC(2026, 8, 6, 14, 0, 0); // Sun 10:00 ET
    const friday = Date.UTC(2026, 8, 4, 14, 0, 0); // Fri 10:00 ET

    expect(mostRecentSessionRange(sunday)).toEqual(regularSessionRange(friday));
  });
});

describe("isWithinTheMostRecentSession", () => {
  // Same as isWithinRegularSession's old cases, but now must pass an
  // explicit `nowMs` for each -- confirmed live, 2026-09: this function
  // exists specifically because those two can differ (see its own
  // comment), so a test can no longer just assume "now" equals
  // referenceMs's own day.
  it("is true at 10:00 ET on a weekday, checked the same day", () => {
    const weekday = Date.UTC(2026, 8, 3, 14, 0, 0); // Thu 10:00 ET
    expect(isWithinTheMostRecentSession(weekday, weekday)).toBe(true);
  });

  it("is false before the 09:30 ET open, checked the same day", () => {
    const beforeOpen = Date.UTC(2026, 8, 3, 13, 0, 0); // 09:00 ET
    expect(isWithinTheMostRecentSession(beforeOpen, beforeOpen)).toBe(false);
  });

  it("is false at and after the 16:00 ET close (half-open interval), checked the same day", () => {
    const atClose = Date.UTC(2026, 8, 3, 20, 0, 0); // 16:00 ET exactly
    const afterClose = Date.UTC(2026, 8, 3, 21, 11, 0); // 17:11 ET
    expect(isWithinTheMostRecentSession(atClose, atClose)).toBe(false);
    expect(isWithinTheMostRecentSession(afterClose, afterClose)).toBe(false);
  });

  it("is true for Friday's own price, checked over the weekend (Saturday)", () => {
    // The exact "don't break this" case: Friday's data is still the
    // most recent real session as of Saturday, so it must stay valid --
    // the fix is about rejecting *stale* prices, not every past one.
    const fridayAt10am = Date.UTC(2026, 8, 4, 14, 0, 0); // Fri 10:00 ET
    const saturday = Date.UTC(2026, 8, 5, 14, 0, 0);
    expect(isWithinTheMostRecentSession(fridayAt10am, saturday)).toBe(true);
  });

  it("is false for a stale price from a real weekday days before the most recent session (the confirmed bug)", () => {
    // Confirmed live, 2026-09, a real Saturday: a Tuesday-afternoon price
    // (a real, valid session moment on its OWN day) was still on file as
    // "latest" with nothing fresher written since -- the old
    // isWithinRegularSession accepted it (it only checked "some
    // session"), which is exactly what corrupted the chart once anchored
    // against a same-day-as-now reference. Must be rejected now: Tuesday
    // is not within Saturday's most recent session (Friday's).
    const tuesdayAt2pm = Date.UTC(2026, 8, 1, 18, 0, 0); // Tue 14:00 ET
    const saturday = Date.UTC(2026, 8, 5, 14, 0, 0);
    expect(isWithinTheMostRecentSession(tuesdayAt2pm, saturday)).toBe(false);
  });

  it("is false on a Saturday 'now' for a Saturday reference (no session happened today)", () => {
    const saturday = Date.UTC(2026, 8, 5, 14, 0, 0); // Sat 10:00 ET
    expect(isWithinTheMostRecentSession(saturday, saturday)).toBe(false);
  });

  it("is false on a Sunday 'now' for a Sunday reference (no session happened today)", () => {
    const sunday = Date.UTC(2026, 8, 6, 14, 0, 0); // Sun 10:00 ET
    expect(isWithinTheMostRecentSession(sunday, sunday)).toBe(false);
  });

  it("defaults nowMs to the real current time when not passed", () => {
    // Exercises the default-parameter path specifically -- every other
    // test above pins `nowMs` explicitly for determinism. Doesn't assert
    // true/false (whether "now" is itself in-session depends on when
    // this test happens to run) -- only that omitting `nowMs` produces
    // the same result as passing the real current time explicitly.
    const now = Date.now();
    expect(isWithinTheMostRecentSession(now)).toBe(isWithinTheMostRecentSession(now, now));
  });
});
