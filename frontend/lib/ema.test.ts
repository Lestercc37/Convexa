import { afterEach, describe, expect, it, vi } from "vitest";

import {
  addEma,
  EMA_COLORS,
  EMA_MAX_LINES,
  EMA_STORAGE_KEY,
  emaValues,
  loadEmas,
  nextEmaColor,
  parseEmaPeriod,
  saveEmas,
  type EmaConfig,
} from "@/lib/ema";

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("parseEmaPeriod", () => {
  it("accepts whole numbers from 1 to 1000 and nothing else", () => {
    expect(parseEmaPeriod("9")).toBe(9);
    expect(parseEmaPeriod(" 200 ")).toBe(200);
    expect(parseEmaPeriod(1000)).toBe(1000);
    for (const bad of ["", "0", "-5", "1001", "3.5", "abc", "1e2", " "]) expect(parseEmaPeriod(bad), bad).toBeNull();
  });
});

describe("addEma", () => {
  it("starts empty, adds any period with the next free colour and refuses duplicates and an invalid period", () => {
    let result = addEma([], "21");
    expect(result.error).toBeNull();
    expect(result.emas).toEqual([{ id: "ema-21", period: 21, color: EMA_COLORS[0], visible: true }]);
    result = addEma(result.emas, 34);
    expect(result.emas.map((ema) => ema.color)).toEqual([EMA_COLORS[0], EMA_COLORS[1]]);
    expect(addEma(result.emas, "21").error).toBe("duplicate");
    expect(addEma(result.emas, "0").error).toBe("invalid");
    expect(addEma(result.emas, "").error).toBe("invalid");
  });

  it("stops at the maximum number of lines", () => {
    let emas: EmaConfig[] = [];
    for (let period = 1; period <= EMA_MAX_LINES; period += 1) emas = addEma(emas, period).emas;
    expect(emas).toHaveLength(EMA_MAX_LINES);
    expect(addEma(emas, 999).error).toBe("max");
  });

  it("reuses a colour freed by a removed line", () => {
    const two = addEma(addEma([], 5).emas, 8).emas;
    expect(nextEmaColor([two[1]])).toBe(EMA_COLORS[0]);
  });
});

describe("emaValues", () => {
  it("is the classic EMA: alpha 2/(period+1) from the first close", () => {
    const { values } = emaValues([], [10, 11, 12, 13], 3); // alpha 0.5
    expect(values).toEqual([10, 10.5, 11.25, 12.125]);
  });

  it("returns one value per close and uses the seed only to warm up, never as points", () => {
    const seeded = emaValues([10, 10, 10, 10], [20, 20], 3);
    expect(seeded.values).toHaveLength(2);
    expect(seeded.values[0]).toBe(15); // 0.5 * 20 + 0.5 * 10
    expect(seeded.values[1]).toBe(17.5);
    const cold = emaValues([], [20, 20], 3);
    expect(cold.values).toEqual([20, 20]); // starts at its first close
  });

  it("flags a line that has fewer bars than its period as still converging", () => {
    expect(emaValues([], [1, 2, 3], 200).warm).toBe(false);
    expect(emaValues(new Array<number>(198).fill(5), [1, 2], 200).warm).toBe(true);
    expect(emaValues([], [], 9)).toEqual({ values: [], warm: false });
  });
});

describe("storage", () => {
  it("round-trips the list and survives garbage, duplicates and blocked storage", () => {
    const emas: EmaConfig[] = [
      { id: "ema-9", period: 9, color: "#f5c542", visible: true },
      { id: "ema-200", period: 200, color: "#4aa8ff", visible: false },
    ];
    saveEmas(emas);
    expect(loadEmas()).toEqual(emas);

    window.localStorage.setItem(EMA_STORAGE_KEY, "not json");
    expect(loadEmas()).toEqual([]);
    window.localStorage.setItem(EMA_STORAGE_KEY, JSON.stringify([emas[0], emas[0], { id: "x", period: 0, color: "red", visible: true }, "junk"]));
    expect(loadEmas()).toEqual([emas[0]]);

    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(loadEmas()).toEqual([]);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => saveEmas(emas)).not.toThrow();
  });
});
