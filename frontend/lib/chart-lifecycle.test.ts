import { describe, expect, it } from "vitest";

import { CHART_LIFECYCLE_MAX_ENTRIES, CHART_LIFECYCLE_STORAGE_KEY, readChartEvents, recordChartEvent } from "./chart-lifecycle";

function fakeStorage(initial?: string) {
  const data = new Map<string, string>();
  if (initial !== undefined) data.set(CHART_LIFECYCLE_STORAGE_KEY, initial);
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
  };
}

describe("chart lifecycle recorder", () => {
  it("appends entries with their timestamp and detail", () => {
    const storage = fakeStorage();
    recordChartEvent("chart-mount", { symbol: "SPX", timeframe: "1m" }, storage, new Date("2026-10-16T20:00:00Z"));
    recordChartEvent("chart-unmount", { symbol: "SPX" }, storage, new Date("2026-10-16T20:00:05Z"));
    expect(readChartEvents(storage)).toEqual([
      { at: "2026-10-16T20:00:00.000Z", event: "chart-mount", detail: { symbol: "SPX", timeframe: "1m" } },
      { at: "2026-10-16T20:00:05.000Z", event: "chart-unmount", detail: { symbol: "SPX" } },
    ]);
  });

  it("keeps only the most recent entries", () => {
    const storage = fakeStorage();
    for (let i = 0; i < CHART_LIFECYCLE_MAX_ENTRIES + 10; i += 1) recordChartEvent("tick", { i }, storage);
    const entries = readChartEvents(storage);
    expect(entries).toHaveLength(CHART_LIFECYCLE_MAX_ENTRIES);
    expect(entries.at(-1)?.detail).toEqual({ i: CHART_LIFECYCLE_MAX_ENTRIES + 9 });
  });

  it("starts over from corrupted storage instead of throwing", () => {
    const storage = fakeStorage("{not json");
    recordChartEvent("page-load", {}, storage);
    expect(readChartEvents(storage)).toHaveLength(1);
  });

  it("never throws when storage is unavailable or refuses writes", () => {
    expect(() => recordChartEvent("page-load", {}, null)).not.toThrow();
    const refusing = { getItem: () => null, setItem: () => { throw new Error("quota"); } };
    expect(() => recordChartEvent("page-load", {}, refusing)).not.toThrow();
    expect(readChartEvents(null)).toEqual([]);
  });
});
