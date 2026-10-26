import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CUBE_MIN_LIMIT,
  CUBE_MIN_STORAGE_KEY,
  CUBE_SIZING_STORAGE_KEY,
  effectiveCubeMin,
  LEGACY_CUBE_MIN,
  loadCubeMins,
  loadCubeSizing,
  parseCubeMin,
  saveCubeMins,
  saveCubeSizing,
} from "@/lib/cube-minimum";

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("parseCubeMin", () => {
  it("accepts whole numbers (with separators) from 1 to the limit and nothing else", () => {
    expect(parseCubeMin("2500")).toBe(2500);
    expect(parseCubeMin(" 2,500 ")).toBe(2500);
    expect(parseCubeMin(20)).toBe(20);
    expect(parseCubeMin(CUBE_MIN_LIMIT)).toBe(CUBE_MIN_LIMIT);
    for (const bad of ["", "0", "-5", "12.5", "abc", String(CUBE_MIN_LIMIT + 1), "1e3"]) expect(parseCubeMin(bad), bad).toBeNull();
  });
});

describe("effectiveCubeMin", () => {
  it("prefers the viewer's own number, then the API suggestion, then the legacy default", () => {
    expect(effectiveCubeMin("SPY", { SPY: 3000 }, { SPY: 2000 })).toEqual({ value: 3000, source: "custom" });
    expect(effectiveCubeMin("SPY", {}, { SPY: 2000 })).toEqual({ value: 2000, source: "auto" });
    expect(effectiveCubeMin("ndx", {}, { NDX: 20 })).toEqual({ value: 20, source: "auto" });
    expect(effectiveCubeMin("AAPL", {}, { SPY: 2000 })).toEqual({ value: LEGACY_CUBE_MIN, source: "default" });
  });
});

describe("storage", () => {
  it("round-trips per symbol and survives garbage and blocked storage", () => {
    saveCubeMins({ SPY: 2500, NDX: 20 });
    expect(loadCubeMins()).toEqual({ SPY: 2500, NDX: 20 });

    window.localStorage.setItem(CUBE_MIN_STORAGE_KEY, JSON.stringify({ SPY: 2500, BAD: 0, "<x>": 5, QQQ: "7", NVDA: 1.5 }));
    expect(loadCubeMins()).toEqual({ SPY: 2500 });
    window.localStorage.setItem(CUBE_MIN_STORAGE_KEY, "not json");
    expect(loadCubeMins()).toEqual({});
    window.localStorage.setItem(CUBE_MIN_STORAGE_KEY, JSON.stringify([1, 2]));
    expect(loadCubeMins()).toEqual({});

    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(loadCubeMins()).toEqual({});
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => saveCubeMins({ SPY: 1 })).not.toThrow();
  });

  it("remembers net or gross sizing, net by default and on anything unreadable or blocked", () => {
    expect(loadCubeSizing()).toBe("net");
    saveCubeSizing("gross");
    expect(loadCubeSizing()).toBe("gross");
    saveCubeSizing("net");
    expect(loadCubeSizing()).toBe("net");
    window.localStorage.setItem(CUBE_SIZING_STORAGE_KEY, "junk");
    expect(loadCubeSizing()).toBe("net");
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(loadCubeSizing()).toBe("net");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => saveCubeSizing("gross")).not.toThrow();
  });
});
