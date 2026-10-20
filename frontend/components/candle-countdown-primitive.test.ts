import { describe, expect, it, vi } from "vitest";
import { CandleCountdownPrimitive, COUNTDOWN_DOWN_COLOR, COUNTDOWN_OFFSET_PX, COUNTDOWN_UP_COLOR, type CandleCountdownReading } from "./candle-countdown-primitive";

function attached(reading: CandleCountdownReading, coordinate: number | null = 200) {
  const state = { reading };
  const primitive = new CandleCountdownPrimitive(() => state.reading);
  const requestUpdate = vi.fn();
  primitive.attached({ series: { priceToCoordinate: () => coordinate }, requestUpdate } as never);
  const view = () => primitive.priceAxisViews()[0];
  return { primitive, requestUpdate, view, state };
}

describe("CandleCountdownPrimitive", () => {
  it("shows the countdown text right under the last price, in the candle's color", () => {
    const { view } = attached({ price: 7741.2, secondsText: "00:29", up: true });
    expect(view().text()).toBe("00:29");
    expect(view().fixedCoordinate?.()).toBe(200 + COUNTDOWN_OFFSET_PX);
    expect(view().backColor()).toBe(COUNTDOWN_UP_COLOR);
    expect(view().visible?.()).toBe(true);
  });

  it("follows the reading while it is drawn (down candle, new price)", () => {
    const { view, state } = attached({ price: 7741.2, secondsText: "00:29", up: true });
    state.reading = { price: 7738.0, secondsText: "00:12", up: false };
    expect(view().text()).toBe("00:12");
    expect(view().backColor()).toBe(COUNTDOWN_DOWN_COLOR);
  });

  it("is hidden without a countdown or without a price on screen", () => {
    expect(attached({ price: 7741.2, secondsText: null, up: true }).view().visible?.()).toBe(false);
    expect(attached({ price: null, secondsText: "00:10", up: true }).view().visible?.()).toBe(false);
    expect(attached({ price: 7741.2, secondsText: "00:10", up: true }, null).view().visible?.()).toBe(false);
  });

  it("asks the chart to redraw on refresh, and stops after detach", () => {
    const { primitive, requestUpdate } = attached({ price: 1, secondsText: "00:01", up: true });
    primitive.refresh();
    expect(requestUpdate).toHaveBeenCalledTimes(1);
    primitive.detached();
    primitive.refresh();
    expect(requestUpdate).toHaveBeenCalledTimes(1);
  });
});
