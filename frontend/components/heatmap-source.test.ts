import { beforeEach, describe, expect, it, vi } from "vitest";
import type { GammaHeatmapResponse } from "@/lib/types";
import { fastBucketSeconds, loadHeatmap, resetHeatmapSourceState } from "./heatmap-source";
import { heatmapFromResponse, prependHistory } from "./gamma-heatmap-primitive";

const api = vi.hoisted(() => ({ getGammaHeatmap: vi.fn() }));
vi.mock("@/lib/api", () => ({ getGammaHeatmap: api.getGammaHeatmap }));

const START = new Date("2026-10-19T13:00:00Z");
const REQUEST = { view: "tactical" as const, metric: "gamma" as const, start: START };

function response(bucket: number, columns: { as_of: string; values: (number | null)[] }[], strikes = [550, 555]): GammaHeatmapResponse {
  return { schema_version: 1, symbol: "SPY", view: "tactical", metric: "gamma", bucket_seconds: bucket, strikes, columns, scale_p98: 9 } as GammaHeatmapResponse;
}

const CYCLE = response(60, [
  { as_of: "2026-10-19T14:00:20Z", values: [1, 2] },
  { as_of: "2026-10-19T14:01:20Z", values: [3, 4] },
  { as_of: "2026-10-19T14:02:20Z", values: [5, 6] },
]);
const FAST = response(10, [
  { as_of: "2026-10-19T14:02:03Z", values: [7, 8] },
  { as_of: "2026-10-19T14:02:13Z", values: [9, 10] },
]);

beforeEach(() => {
  api.getGammaHeatmap.mockReset();
  resetHeatmapSourceState();
});

describe("heatmapFromResponse", () => {
  it("floors each column to the response's own bucket: a minute for the cycle, 10 s for the fast layer", () => {
    expect(heatmapFromResponse(CYCLE).columns.map((column) => column.time)).toEqual([
      Date.parse("2026-10-19T14:00:00Z") / 1000,
      Date.parse("2026-10-19T14:01:00Z") / 1000,
      Date.parse("2026-10-19T14:02:00Z") / 1000,
    ]);
    expect(heatmapFromResponse(FAST).columns.map((column) => column.time)).toEqual([
      Date.parse("2026-10-19T14:02:00Z") / 1000,
      Date.parse("2026-10-19T14:02:10Z") / 1000,
    ]);
  });
});

describe("prependHistory", () => {
  it("keeps the cycle's columns before the first fast column and unions the strikes", () => {
    const history = heatmapFromResponse(CYCLE);
    const live = heatmapFromResponse(response(10, [{ as_of: "2026-10-19T14:02:03Z", values: [7, 8] }], [555, 560]));
    const merged = prependHistory(history, live);
    expect(merged.strikes).toEqual([550, 555, 560]);
    // 14:00 and 14:01 stay (before 14:02:00, the first fast column); the 14:02 cycle column is replaced by the fast one.
    expect(merged.columns.map((column) => column.time)).toEqual([
      Date.parse("2026-10-19T14:00:00Z") / 1000,
      Date.parse("2026-10-19T14:01:00Z") / 1000,
      Date.parse("2026-10-19T14:02:00Z") / 1000,
    ]);
    expect(merged.columns[0].values).toEqual([1, 2, null]);
    expect(merged.columns[2].values).toEqual([null, 7, 8]);
    expect(merged.scale).toBe(9);
  });

  it("returns the live matrix untouched when there is no history, and the history when there is nothing live", () => {
    const live = heatmapFromResponse(FAST);
    expect(prependHistory(null, live)).toBe(live);
    const history = heatmapFromResponse(CYCLE);
    expect(prependHistory(history, { ...live, columns: [] }).columns).toHaveLength(0);
  });
});

describe("loadHeatmap", () => {
  it("uses the fast layer when it has columns, with the morning from the cycle in front of it", async () => {
    api.getGammaHeatmap.mockImplementation((_symbol: string, options: { source?: string }) =>
      Promise.resolve(options.source === "fast" ? FAST : CYCLE),
    );
    const loaded = await loadHeatmap("SPY", REQUEST);
    expect(loaded.fast).toBe(true);
    expect(api.getGammaHeatmap).toHaveBeenCalledWith("SPY", expect.objectContaining({ source: "fast", bucketSeconds: 10 }), undefined);
    expect(loaded.data.columns.map((column) => column.values)).toEqual([[1, 2], [3, 4], [7, 8], [9, 10]]);
  });

  it("asks NDX at 30 s buckets (150 strikes at 10 s would pass the endpoint's row cap)", async () => {
    expect(fastBucketSeconds("NDX")).toBe(30);
    expect(fastBucketSeconds("ndx")).toBe(30);
    expect(fastBucketSeconds("SPX")).toBe(10);
  });

  it("falls back to the cycle's one-minute columns when the fast layer is empty, and does not ask again for a minute", async () => {
    api.getGammaHeatmap.mockImplementation((_symbol: string, options: { source?: string }) =>
      Promise.resolve(options.source === "fast" ? response(10, []) : CYCLE),
    );
    const first = await loadHeatmap("SPY", REQUEST);
    expect(first.fast).toBe(false);
    expect(first.data.columns).toHaveLength(3);
    expect(api.getGammaHeatmap).toHaveBeenCalledTimes(2); // fast (empty) + cycle

    api.getGammaHeatmap.mockClear();
    const second = await loadHeatmap("SPY", REQUEST);
    expect(second.fast).toBe(false);
    expect(api.getGammaHeatmap).toHaveBeenCalledTimes(1); // cycle only
    expect(api.getGammaHeatmap).toHaveBeenCalledWith("SPY", expect.objectContaining({ bucketSeconds: 60 }), undefined);
  });

  it("falls back to the cycle when the fast request fails (older backend, table missing)", async () => {
    api.getGammaHeatmap.mockImplementation((_symbol: string, options: { source?: string }) =>
      options.source === "fast" ? Promise.reject(new Error("500")) : Promise.resolve(CYCLE),
    );
    const loaded = await loadHeatmap("SPY", REQUEST);
    expect(loaded.fast).toBe(false);
    expect(loaded.data.columns).toHaveLength(3);
  });

  it("keeps drawing the fast columns when only the history read fails", async () => {
    api.getGammaHeatmap.mockImplementation((_symbol: string, options: { source?: string }) =>
      options.source === "fast" ? Promise.resolve(FAST) : Promise.reject(new Error("boom")),
    );
    const loaded = await loadHeatmap("SPY", REQUEST);
    expect(loaded.fast).toBe(true);
    expect(loaded.data.columns).toHaveLength(2);
  });

  it("reads the cycle's history once a minute, not on every 10-second poll", async () => {
    api.getGammaHeatmap.mockImplementation((_symbol: string, options: { source?: string }) =>
      Promise.resolve(options.source === "fast" ? FAST : CYCLE),
    );
    await loadHeatmap("SPY", REQUEST);
    await loadHeatmap("SPY", REQUEST);
    await loadHeatmap("SPY", REQUEST);
    const cycleCalls = api.getGammaHeatmap.mock.calls.filter(([, options]) => options.source === undefined);
    const fastCalls = api.getGammaHeatmap.mock.calls.filter(([, options]) => options.source === "fast");
    expect(fastCalls).toHaveLength(3);
    expect(cycleCalls).toHaveLength(1);
  });

  it("lets an aborted request through instead of treating it as 'no fast layer'", async () => {
    api.getGammaHeatmap.mockRejectedValue(new DOMException("aborted", "AbortError"));
    await expect(loadHeatmap("SPY", REQUEST)).rejects.toThrow("aborted");
    api.getGammaHeatmap.mockImplementation((_symbol: string, options: { source?: string }) =>
      Promise.resolve(options.source === "fast" ? FAST : CYCLE),
    );
    expect((await loadHeatmap("SPY", REQUEST)).fast).toBe(true); // the abort did not mark the layer unavailable
  });
});

describe("loadHeatmap: incremental fast polls", () => {
  const BUCKET_START = Date.parse("2026-10-19T14:02:10Z");

  function fastOrCycle(fastResponses: GammaHeatmapResponse[]) {
    let calls = 0;
    api.getGammaHeatmap.mockImplementation((_symbol: string, options: { source?: string }) => {
      if (options.source !== "fast") return Promise.resolve(CYCLE);
      return Promise.resolve(fastResponses[Math.min(calls++, fastResponses.length - 1)]);
    });
  }

  it("asks the whole window the first time and only the last bucket onward afterwards, merging the tail", async () => {
    const tail = response(10, [
      { as_of: "2026-10-19T14:02:13Z", values: [11, 12] }, // the last bucket again (refreshed)
      { as_of: "2026-10-19T14:02:23Z", values: [13, 14] }, // a new one
    ]);
    fastOrCycle([FAST, tail]);

    const first = await loadHeatmap("SPY", REQUEST);
    const second = await loadHeatmap("SPY", REQUEST);

    const fastCalls = api.getGammaHeatmap.mock.calls.filter((call) => call[1].source === "fast");
    expect(fastCalls[0][1].start).toBe(START); // the full window
    expect(fastCalls[1][1].start).toEqual(new Date(BUCKET_START)); // from the last column's bucket
    expect(first.data.columns.map((column) => column.values)).toEqual([[1, 2], [3, 4], [7, 8], [9, 10]]);
    expect(second.data.columns.map((column) => column.values)).toEqual([[1, 2], [3, 4], [7, 8], [11, 12], [13, 14]]);
    expect(second.fast).toBe(true);
  });

  it("keeps the day's colour scale of the last full response: a tail's own p98 never changes it (the 'Escala del día' flicker)", async () => {
    const full = { ...FAST, scale_p98: 9 };
    const tail = { ...response(10, [{ as_of: "2026-10-19T14:02:13Z", values: [90, 95] }]), scale_p98: 95 };
    api.getGammaHeatmap.mockImplementation((_symbol: string, options: { source?: string }) => {
      if (options.source !== "fast") return Promise.resolve({ ...CYCLE, scale_p98: 5 });
      return Promise.resolve(api.getGammaHeatmap.mock.calls.filter((call) => call[1].source === "fast").length === 1 ? full : tail);
    });
    const first = await loadHeatmap("SPY", REQUEST);
    const second = await loadHeatmap("SPY", REQUEST);
    const third = await loadHeatmap("SPY", REQUEST);
    expect(first.data.scale).toBe(9);
    expect(second.data.scale).toBe(9); // not 95
    expect(third.data.scale).toBe(9);
  });

  it("unions the strikes of the tail with the ones already held", async () => {
    const tail = response(10, [{ as_of: "2026-10-19T14:02:13Z", values: [20, 21] }], [555, 560]);
    fastOrCycle([FAST, tail]);
    await loadHeatmap("SPY", REQUEST);
    const second = await loadHeatmap("SPY", REQUEST);
    expect(second.data.strikes).toEqual([550, 555, 560]);
    expect(second.data.columns.at(-1)!.values).toEqual([null, 20, 21]);
    expect(second.data.columns.find((column) => column.time === Date.parse("2026-10-19T14:02:00Z") / 1000)!.values).toEqual([7, 8, null]);
  });

  it("asks the whole window again after five minutes and when the key changes", async () => {
    fastOrCycle([FAST]);
    const now = vi.spyOn(Date, "now");
    now.mockReturnValue(1_000_000);
    await loadHeatmap("SPY", REQUEST);
    now.mockReturnValue(1_000_000 + 4 * 60_000);
    await loadHeatmap("SPY", REQUEST);
    now.mockReturnValue(1_000_000 + 5 * 60_000 + 1);
    await loadHeatmap("SPY", REQUEST);
    await loadHeatmap("SPY", { ...REQUEST, metric: "vanna" });
    const starts = api.getGammaHeatmap.mock.calls.filter((call) => call[1].source === "fast").map((call) => call[1].start);
    expect(starts[0]).toBe(START);
    expect(starts[1]).toEqual(new Date(BUCKET_START)); // within five minutes: incremental
    expect(starts[2]).toBe(START); // past five minutes: full again
    expect(starts[3]).toBe(START); // another metric has its own matrix
    now.mockRestore();
  });

  it("keeps working when a tail request fails: falls back to the cycle's columns for a minute, then asks again", async () => {
    let fastCalls = 0;
    api.getGammaHeatmap.mockImplementation((_symbol: string, options: { source?: string }) => {
      if (options.source !== "fast") return Promise.resolve(CYCLE);
      fastCalls += 1;
      return fastCalls === 2 ? Promise.reject(new Error("boom")) : Promise.resolve(FAST);
    });
    await loadHeatmap("SPY", REQUEST);
    const afterFailure = await loadHeatmap("SPY", REQUEST);
    expect(afterFailure.fast).toBe(false);
    expect(afterFailure.data.columns).toHaveLength(3); // the cycle's own columns
  });
});
