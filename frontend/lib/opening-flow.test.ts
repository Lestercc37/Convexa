import { describe, expect, it } from "vitest";

import { formatDeltaFlow, formatPremium, summarizeOpeningAlerts, summarizeOpeningFlow } from "@/lib/opening-flow";
import type { MinuteFlowResponse, WhaleAlert } from "@/lib/types";

const OPEN = Date.parse("2026-10-22T13:30:00Z") / 1000; // 09:30 ET

function flow(minutes: MinuteFlowResponse["minutes"]): MinuteFlowResponse {
  return { schema_version: 1, symbol: "SPX", minutes };
}

function alert(overrides: Partial<WhaleAlert>): WhaleAlert {
  return {
    symbol: "SPX",
    contract: "SPXW261022C07700000",
    type: "UNUSUAL",
    amount: 1_000_000,
    timestamp: "2026-10-22T13:36:00Z",
    estimated_buy_volume: 80,
    estimated_sell_volume: 20,
    quote_unavailable: false,
    moneyness: "ATM",
    near_gamma_level: null,
    delta: 0.5,
    contracts: 400,
    repeat_count: 1,
    ...overrides,
  };
}

describe("summarizeOpeningFlow", () => {
  it("sums calls and puts from the open and ignores the minutes before it", () => {
    const summary = summarizeOpeningFlow(
      flow([
        { minute: "2026-10-22T13:00:00Z", calls_delta_flow: 999, puts_delta_flow: 999, classified_share: 0.9 }, // pre-market, not counted
        { minute: "2026-10-22T13:30:00Z", calls_delta_flow: 100, puts_delta_flow: -40, classified_share: 0.4 },
        { minute: "2026-10-22T13:31:00Z", calls_delta_flow: 50, puts_delta_flow: 10, classified_share: 0.6 },
      ]),
      OPEN,
    );
    expect(summary).toEqual({ calls: 150, puts: -30, net: 120, classifiedShare: 0.5, minutes: 2 });
  });

  it("returns null with no response or no minute since the open, and a null share when no minute reports one", () => {
    expect(summarizeOpeningFlow(null, OPEN)).toBeNull();
    expect(summarizeOpeningFlow(flow([{ minute: "2026-10-22T13:00:00Z", calls_delta_flow: 1, puts_delta_flow: 1, classified_share: null }]), OPEN)).toBeNull();
    expect(summarizeOpeningFlow(flow([{ minute: "2026-10-22T13:30:00Z", calls_delta_flow: 1, puts_delta_flow: 2, classified_share: null }]), OPEN)?.classifiedShare).toBeNull();
  });
});

describe("summarizeOpeningAlerts", () => {
  it("buying a call or selling a put is bullish; selling a call or buying a put is bearish", () => {
    const summary = summarizeOpeningAlerts(
      [
        alert({ amount: 1_000_000 }), // buy call -> bullish
        alert({ contract: "SPXW261022P07600000", estimated_buy_volume: 10, estimated_sell_volume: 90, amount: 2_000_000 }), // sell put -> bullish
        alert({ contract: "SPXW261022C07750000", estimated_buy_volume: 10, estimated_sell_volume: 90, amount: 500_000 }), // sell call -> bearish
        alert({ contract: "SPXW261022P07650000", amount: 300_000 }), // buy put -> bearish
      ],
      OPEN,
    );
    expect(summary.bullishPremium).toBe(3_000_000);
    expect(summary.bearishPremium).toBe(800_000);
    expect(summary.bullishCount).toBe(2);
    expect(summary.bearishCount).toBe(2);
    expect(summary.bullishDeq).toBe(400);
  });

  it("alerts without a clear side are counted apart and never added to a direction", () => {
    const summary = summarizeOpeningAlerts(
      [alert({ estimated_buy_volume: 50, estimated_sell_volume: 50 }), alert({ quote_unavailable: true }), alert({ amount: 7 })],
      OPEN,
    );
    expect(summary.unclearCount).toBe(2);
    expect(summary.bullishPremium).toBe(7);
    expect(summary.bearishPremium).toBe(0);
  });

  it("uses the trade time when the alert has one and ignores alerts before the open", () => {
    const summary = summarizeOpeningAlerts(
      [
        alert({ timestamp: "2026-10-22T13:45:00Z", trade_start: "2026-10-22T13:20:00Z" }), // traded before the open
        alert({ timestamp: "2026-10-22T13:45:00Z", trade_start: "2026-10-22T13:31:00Z", amount: 5 }),
      ],
      OPEN,
    );
    expect(summary.bullishCount).toBe(1);
    expect(summary.bullishPremium).toBe(5);
  });
});

describe("formatting", () => {
  it("signs the delta flow with a real minus and groups thousands", () => {
    expect(formatDeltaFlow(12345.4)).toBe("+12,345");
    expect(formatDeltaFlow(-4567)).toBe("\u22124,567");
    expect(formatDeltaFlow(0.2)).toBe("0");
  });

  it("writes premium compactly", () => {
    expect(formatPremium(12_340_000)).toBe("$12M");
    expect(formatPremium(3_450_000)).toBe("$3.5M");
    expect(formatPremium(450_400)).toBe("$450K");
    expect(formatPremium(0)).toBe("$0");
  });
});
