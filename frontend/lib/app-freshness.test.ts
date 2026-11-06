import { describe, expect, it } from "vitest";
import { IDLE_BEFORE_UPDATE_MS, MIN_RELOAD_GAP_MS, shouldReload, type FreshnessState } from "./app-freshness";

const base: FreshnessState = { now: 10_000_000, visible: true, wokeUp: false, newVersion: false, lastInteractionAt: 10_000_000, lastAutoReloadAt: 0 };

describe("automatic reload", () => {
  it("reloads right after the computer wakes, once the page is visible", () => {
    expect(shouldReload({ ...base, wokeUp: true })).toBe(true);
    expect(shouldReload({ ...base, wokeUp: true, visible: false })).toBe(false);
  });

  it("takes a new version only when the trader has been idle for a while", () => {
    expect(shouldReload({ ...base, newVersion: true })).toBe(false);
    expect(shouldReload({ ...base, newVersion: true, lastInteractionAt: base.now - IDLE_BEFORE_UPDATE_MS })).toBe(true);
  });

  it("never reloads twice within a minute (loop guard) and does nothing when all is fresh", () => {
    expect(shouldReload({ ...base, wokeUp: true, lastAutoReloadAt: base.now - MIN_RELOAD_GAP_MS + 1 })).toBe(false);
    expect(shouldReload(base)).toBe(false);
  });
});
