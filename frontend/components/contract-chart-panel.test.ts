import { describe, expect, it } from "vitest";
import type { ContractPoint } from "@/lib/types";
import { dayHigh, rangeBars } from "./contract-chart-panel";

const point = (minute: number, pnl: number, high = pnl, low = pnl): ContractPoint => ({
  time: new Date(Date.parse("2026-11-02T19:44:00Z") + minute * 60_000).toISOString(),
  bid: 0,
  ask: 0,
  mid: 0,
  pnl,
  pnl_high: high,
  pnl_low: low,
});

describe("contract chart range", () => {
  // SPXW 11-02 P7620 (Lester 2026-11-02): the line peaked at +$4,420 while the contract printed 64.81 (+$4,581) inside the minute.
  const points = [point(0, 3335), point(4, 4420, 4581, 4310), point(5, 4325), point(8, 3495, 3600, -20)];

  it("draws a thin bar only where the minute went beyond its last quote, colored by the side of zero", () => {
    const bars = rangeBars(points);
    expect(bars.map((bar) => [bar.high, bar.low, bar.open, bar.close])).toEqual([
      [4581, 4310, 4420, 4420],
      [3600, -20, 3495, 3495],
    ]);
    expect(bars[0].color).toContain("0, 220, 90");
  });

  it("the header's high is the highest price inside any minute, not the highest line point", () => {
    expect(dayHigh(points)?.pnl_high).toBe(4581);
    expect(dayHigh([])).toBeNull();
  });
});
