import { describe, expect, it } from "vitest";
import { profileSource } from "./profile-source";

const base = { showHeatmap: true, sidesOn: false, metric: "gamma" as const, heatmapIsFast: true, fastTailHasBars: false };

describe("GEX profile source (Lester 2026-11-05: the fast layer only on the 0DTE tabs)", () => {
  it("follows the fast heatmap on the 0DTE tabs, by OI and by volume", () => {
    expect(profileSource({ ...base, view: "0dte" })).toBe("heatmap");
    expect(profileSource({ ...base, view: "0dte_volume", metric: "absolute_gamma" })).toBe("heatmap");
  });

  it("keeps the cycle's profile on every other tab, and when the heatmap is not fast", () => {
    for (const view of ["tactical", "1dte", "2dte", "90d"] as const) expect(profileSource({ ...base, view })).toBe("endpoint");
    expect(profileSource({ ...base, view: "0dte", heatmapIsFast: false })).toBe("endpoint");
  });

  it("reads only the fast tail with the heatmap off, and falls back to the cycle when the tail has nothing", () => {
    expect(profileSource({ ...base, view: "0dte", showHeatmap: false, fastTailHasBars: true })).toBe("fast-tail");
    expect(profileSource({ ...base, view: "0dte", showHeatmap: false, fastTailHasBars: false })).toBe("endpoint");
    expect(profileSource({ ...base, view: "1dte", showHeatmap: false, fastTailHasBars: true })).toBe("endpoint");
  });

  it("keeps the heatmap's own figures (delta, vanna, charm, total, sides) on the heatmap as before", () => {
    expect(profileSource({ ...base, view: "1dte", metric: "delta", heatmapIsFast: false })).toBe("heatmap");
    expect(profileSource({ ...base, view: "1dte", sidesOn: true })).toBe("heatmap");
  });
});
