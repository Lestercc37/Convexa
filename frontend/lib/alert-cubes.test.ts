import { describe, expect, it } from "vitest";
import type { WhaleAlert } from "@/lib/types";
import {
  buildCubes,
  conditionCategory,
  conditionShares,
  cubeSide,
  cubeSizePx,
  occStrike,
  trialLabel,
} from "./alert-cubes";

function alert(overrides: Partial<WhaleAlert> = {}): WhaleAlert {
  return {
    symbol: "SPX",
    contract: "SPXW261016C07720000",
    type: "UNUSUAL",
    amount: 1_550_000,
    timestamp: "2026-10-16T14:52:05Z",
    estimated_buy_volume: 50_000,
    estimated_sell_volume: 1_500_000,
    quote_unavailable: false,
    moneyness: "ITM",
    near_gamma_level: null,
    repeat_count: 2,
    delta: 0.84,
    contracts: 529,
    ...overrides,
  };
}

describe("cubeSizePx", () => {
  it("uses three sizes: 200-400 small, 400-600 medium, 600 and up large", () => {
    expect([100, 399, 400, 599, 600, 5000].map(cubeSizePx)).toEqual([6, 6, 9, 9, 12, 12]);
  });
});

describe("cubeSide", () => {
  it("is buy from 60% of the estimated flow, sell up to 40%, otherwise no clear side", () => {
    const side = (buy: number, sell: number, quote_unavailable = false) =>
      cubeSide({ estimated_buy_volume: buy, estimated_sell_volume: sell, quote_unavailable });
    expect(side(60, 40)).toBe("buy");
    expect(side(59, 41)).toBe("mixed");
    expect(side(40, 60)).toBe("sell");
    expect(side(41, 59)).toBe("mixed");
    expect(side(0, 0)).toBe("mixed");
    expect(side(90, 10, true)).toBe("mixed"); // no quote: the split is not a measurement
  });
});

describe("occStrike", () => {
  it("reads the strike from the OCC symbol's last 8 digits, in thousandths", () => {
    expect(occStrike("SPXW261016C07720000")).toBe(7720);
    expect(occStrike("SPY260220P00540500")).toBe(540.5);
    expect(occStrike("garbage")).toBeNull();
  });
});

describe("buildCubes", () => {
  it("sizes a cube by contracts x |delta| and keeps the call/put, strike and side", () => {
    const [cube] = buildCubes([alert()]);
    expect(cube).toMatchObject({ strike: 7720, right: "C", deq: 444, size: 9, side: "sell", zone: "ITM" });
    expect(cube.time).toBe(Date.parse("2026-10-16T14:52:05Z") / 1000);
  });

  it("leaves out alerts that cannot be sized (no delta or no contracts) and the ones under the minimum", () => {
    const cubes = buildCubes([
      alert({ delta: null }),
      alert({ contracts: null, contract: "SPXW261016C07725000" }),
      alert({ delta: 0.3, contracts: 500, contract: "SPXW261016P07700000", moneyness: "OTM" }), // 150 < 200
      alert({ delta: 0.5, contracts: 400, contract: "SPXW261016P07710000", moneyness: "ATM" }), // 200
    ]);
    expect(cubes.map((cube) => cube.strike)).toEqual([7710]);
    expect(buildCubes([alert({ delta: 0.3, contracts: 500, moneyness: "OTM" })], { minDeq: 100, showFar: false })).toHaveLength(1);
  });

  it("hides 'Lejos' alerts unless asked for", () => {
    const far = alert({ moneyness: "FAR", delta: 0.13, contracts: 3000 });
    expect(buildCubes([far])).toHaveLength(0);
    expect(buildCubes([far], { minDeq: 200, showFar: true })).toHaveLength(1);
  });

  it("gives two alerts of the same contract and minute different ids", () => {
    const first = alert({ type: "UNUSUAL" });
    const second = alert({ type: "SUSTAINED_FLOW" });
    const ids = buildCubes([first, second]).map((cube) => cube.id);
    expect(new Set(ids).size).toBe(2);
  });
});

describe("conditionCategory / conditionShares", () => {
  it("maps ThetaData's trade-condition codes to categories", () => {
    expect([18, 95, 125, 126, 127, 128, 129, 130, 144, 148, 40, 7].map(conditionCategory)).toEqual([
      "simple", "sweep", "auction", "auction", "cross", "cross", "floor", "multileg", "multileg", "cancel", "cancel", "other",
    ]);
  });

  it("gives the premium share per category, largest first, summing codes of the same category", () => {
    expect(conditionShares({ "130": 60_000, "131": 20_000, "18": 15_000, "125": 5_000 })).toEqual([
      { category: "multileg", percent: 80 },
      { category: "simple", percent: 15 },
      { category: "auction", percent: 5 },
    ]);
    expect(conditionShares(null)).toBeNull();
    expect(conditionShares({})).toBeNull();
  });
});

describe("trialLabel", () => {
  it("applies the 60/40 rule to the simple-condition premium, and 'none' under half of the alert's premium", () => {
    expect(trialLabel({ buy: 90, sell: 10 }, 100)).toBe("buy");
    expect(trialLabel({ buy: 10, sell: 90 }, 100)).toBe("sell");
    expect(trialLabel({ buy: 50, sell: 50 }, 100)).toBe("mixed");
    expect(trialLabel({ buy: 20, sell: 20 }, 100)).toBe("none");
    expect(trialLabel(null, 100)).toBeNull();
  });
});
