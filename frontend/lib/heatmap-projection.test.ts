import { describe, expect, it } from "vitest";
import { projectsSession, sessionSlots } from "./heatmap-projection";

describe("heatmap painted to the close (ES trial)", () => {
  it("applies to ES only", () => {
    expect(projectsSession("ES")).toBe(true);
    expect(projectsSession("es")).toBe(true);
    expect(projectsSession("SPX")).toBe(false);
    expect(projectsSession("NQ")).toBe(false);
  });

  it("lists the session's candle slots up to the close, bucketed like the candles", () => {
    const open = 1_700_000_000 - (1_700_000_000 % 3600) + 1800; // a :30 open
    const close = open + 6.5 * 3600;
    const minutes = sessionSlots(open, close, 60);
    expect(minutes).toHaveLength(390);
    expect(minutes[0]).toBe(open);
    expect(minutes.at(-1)).toBe(close - 60);
    // Hourly candles are bucketed on the hour: the 09:30 open falls in the 09:00 bucket, the last one is 15:00.
    const hours = sessionSlots(open, close, 3600);
    expect(hours[0]).toBe(open - 1800);
    expect(hours).toHaveLength(7);
  });
});
