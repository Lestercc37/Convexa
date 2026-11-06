import { describe, expect, it } from "vitest";
import { ORDER_FLOW_COLORS, orderFlowColor } from "./order-flow-events-primitive";

describe("order-flow event colours", () => {
  it("green / red by who wins, blue / orange for big trades", () => {
    expect(orderFlowColor({ type: "absorption", side: "buy" })).toBe(ORDER_FLOW_COLORS.buy);
    expect(orderFlowColor({ type: "stacked_imbalance", side: "sell" })).toBe(ORDER_FLOW_COLORS.sell);
    expect(orderFlowColor({ type: "big_trade", side: "buy" })).toBe(ORDER_FLOW_COLORS.bigBuy);
    expect(orderFlowColor({ type: "big_trade", side: "sell" })).toBe(ORDER_FLOW_COLORS.bigSell);
  });
});
