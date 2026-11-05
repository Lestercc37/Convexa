import { describe, expect, it } from "vitest";
import type { StrikeFlowRow } from "@/lib/types";
import { POLLING_INTERVAL_MS } from "@/lib/polling";
import { LIVE_CHAIN_POLL_MS, chainPollMs, levelLabels, visibleRows } from "./flow-chain-panel";

const zero = { m5: 0, m15: 0, m30: 0, m60: 0 };
const row = (strike: number, price: number | null = strike): StrikeFlowRow => ({
  strike,
  price,
  net: zero,
  calls: zero,
  puts: zero,
  net_day: 0,
  packages_15m: 0,
  contracts_signed_day: 0,
  contracts_package_day: 0,
  contracts_other_day: 0,
});

describe("flow chain rows", () => {
  it("shows the highest strike on top and at most 14 strikes on each side of the price", () => {
    const rows = Array.from({ length: 60 }, (_, i) => row(7500 + i * 5));
    const shown = visibleRows(rows, 7652);
    expect(shown).toHaveLength(28);
    expect(shown[0].strike).toBe(7720);
    expect(shown[13].strike).toBe(7655);
    expect(shown[14].strike).toBe(7650);
    expect(shown.at(-1)?.strike).toBe(7585);
  });

  it("sorts by the symbol's own price units when the flow comes from another symbol (NQ reads QQQ)", () => {
    const shown = visibleRows([row(600, 25000), row(602, 25080), row(598, 24920)], null);
    expect(shown.map((r) => r.strike)).toEqual([602, 600, 598]);
  });

  it("puts each level on the nearest row, only within one strike step", () => {
    const rows = [row(7660), row(7655), row(7650), row(7645)];
    const labels = levelLabels(rows, { abs: 7650, callWall: 7661, putWall: 7500, flip: 7647 });
    expect(labels.get(7650)).toEqual(["ABS"]);
    expect(labels.get(7660)).toEqual(["CW"]);
    expect(labels.get(7645)).toEqual(["Flip"]);
    expect([...labels.values()].flat()).not.toContain("PW");
  });
});

describe("flow chain poll", () => {
  it("re-reads every 5 s while the data is live and every 30 s for a past session, after the close or after a failed read", () => {
    const now = Date.parse("2026-11-06T15:00:30Z");
    expect(chainPollMs("2026-11-06T15:00:29Z", now)).toBe(LIVE_CHAIN_POLL_MS);
    expect(chainPollMs("2026-11-05T21:00:00Z", now)).toBe(POLLING_INTERVAL_MS);
    expect(chainPollMs("2026-11-06T14:58:00Z", now)).toBe(POLLING_INTERVAL_MS);
    expect(chainPollMs(null, now)).toBe(POLLING_INTERVAL_MS);
  });
});
