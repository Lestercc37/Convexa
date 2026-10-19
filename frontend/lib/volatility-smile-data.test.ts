import { describe, expect, it } from "vitest";
import type { OptionContract } from "@/lib/types";
import { smilePointsFromChain } from "./volatility-smile-data";

const contract = (strike: number, type: "call" | "put", iv: number, bid: number, ask: number): OptionContract => ({
  occ_symbol: `X${strike}${type}`, strike, expiration: "2099-01-01", type, bid, ask, iv, delta: 0.5, gamma: 0.01, theta: 0, vega: 0, charm: 0, vanna: 0, open_interest: 10, volume: 10,
});

// Spot 1000: the window is +-15 points.
const SPOT = 1000;
const byStrike = (a: number, b: number) => a - b;

describe("smilePointsFromChain", () => {
  it("keeps calls AND puts at every strike, in and out of the money, as the old panel did", () => {
    const points = smilePointsFromChain(
      [
        contract(990, "call", 0.2, 14.9, 15.1), // ITM call: intrinsic 10, mid 15, time value 5 vs half spread 0.1: readable
        contract(990, "put", 0.21, 4.9, 5.1),
        contract(1010, "call", 0.19, 4.9, 5.1),
        contract(1010, "put", 0.22, 14.9, 15.1), // ITM put, readable
      ],
      SPOT,
    );
    expect(points).toHaveLength(4);
    expect(points.filter((point) => point.type === "call").map((point) => point.strike).sort(byStrike)).toEqual([990, 1010]);
    expect(points.filter((point) => point.type === "put").map((point) => point.strike).sort(byStrike)).toEqual([990, 1010]);
  });

  it("an in-the-money quote with no time value is drawn like any other: its IV is what the provider gave", () => {
    // put 1010 with spot 1000: intrinsic 10, mid 10.2, time value 0.2 below half the spread
    expect(smilePointsFromChain([contract(1010, "put", 0.12, 9.7, 10.7)], SPOT)).toEqual([{ strike: 1010, iv: 0.12, type: "put" }]);
  });

  it("IV = 0 (no solution) and strikes outside the +-1.5% window stay out", () => {
    const points = smilePointsFromChain(
      [contract(1005, "put", 0, 5, 5.2), contract(1500, "call", 0.4, 1, 1.1), contract(1000, "call", 0.2, 5, 5.2)],
      SPOT,
    );
    expect(points).toEqual([{ strike: 1000, iv: 0.2, type: "call" }]);
  });
});
