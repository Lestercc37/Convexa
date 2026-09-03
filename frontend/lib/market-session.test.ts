import { describe, expect, it } from "vitest";
import { isWithinRegularSession, regularSessionRange } from "./market-session";

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

describe("isWithinRegularSession", () => {
  it("is true at 10:00 ET on a weekday", () => {
    expect(isWithinRegularSession(Date.UTC(2026, 8, 3, 14, 0, 0))).toBe(true); // Thu
  });

  it("is false before the 09:30 ET open", () => {
    expect(isWithinRegularSession(Date.UTC(2026, 8, 3, 13, 0, 0))).toBe(false); // 09:00 ET
  });

  it("is false at and after the 16:00 ET close (half-open interval)", () => {
    expect(isWithinRegularSession(Date.UTC(2026, 8, 3, 20, 0, 0))).toBe(false); // 16:00 ET exactly
    expect(isWithinRegularSession(Date.UTC(2026, 8, 3, 21, 11, 0))).toBe(false); // 17:11 ET
  });

  it("is false on a Saturday, regardless of time of day", () => {
    expect(isWithinRegularSession(Date.UTC(2026, 8, 5, 14, 0, 0))).toBe(false); // Sat 10:00 ET
  });

  it("is false on a Sunday, regardless of time of day", () => {
    expect(isWithinRegularSession(Date.UTC(2026, 8, 6, 14, 0, 0))).toBe(false); // Sun 10:00 ET
  });
});
