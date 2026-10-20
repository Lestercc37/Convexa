import { describe, expect, it } from "vitest";
import type { MinuteFlowResponse } from "@/lib/types";
import { FLOW_DOWN_COLOR, FLOW_UP_COLOR, flowBarsFromResponse } from "./minute-flow";

const response = (minutes: [string, number, number][]): MinuteFlowResponse => ({
  schema_version: 1,
  symbol: "SPX",
  minutes: minutes.map(([minute, calls, puts]) => ({ minute, calls_delta_flow: calls, puts_delta_flow: puts, classified_share: 0.5 })),
});

describe("flowBarsFromResponse", () => {
  it("one bar per minute for 1m candles, positive green and negative red, calls and puts apart", () => {
    const bars = flowBarsFromResponse(response([["2026-10-19T14:31:00Z", 10, -4], ["2026-10-19T14:32:00Z", -2, 3]]), "1m");
    const t = Date.parse("2026-10-19T14:31:00Z") / 1000;
    expect(bars.calls).toEqual([{ time: t, value: 10, color: FLOW_UP_COLOR }, { time: t + 60, value: -2, color: FLOW_DOWN_COLOR }]);
    expect(bars.puts).toEqual([{ time: t, value: -4, color: FLOW_DOWN_COLOR }, { time: t + 60, value: 3, color: FLOW_UP_COLOR }]);
  });

  it("sums the minutes of a candle bucket (5m) so each bar sits under its candle", () => {
    const bars = flowBarsFromResponse(
      response([["2026-10-19T14:30:00Z", 1, 1], ["2026-10-19T14:34:00Z", 2, -5], ["2026-10-19T14:35:00Z", 4, 0]]),
      "5m",
    );
    expect(bars.calls.map((bar) => bar.value)).toEqual([3, 4]);
    expect(bars.puts.map((bar) => bar.value)).toEqual([-4, 0]);
    expect(bars.calls[1].time - bars.calls[0].time).toBe(300);
  });

  it("is empty without minutes and skips a bad timestamp", () => {
    expect(flowBarsFromResponse(response([]), "1m")).toEqual({ calls: [], puts: [] });
    expect(flowBarsFromResponse(response([["not a time", 1, 1]]), "1m")).toEqual({ calls: [], puts: [] });
  });
});
