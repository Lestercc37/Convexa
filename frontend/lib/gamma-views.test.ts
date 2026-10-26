import { describe, expect, it } from "vitest";
import { GAMMA_VIEW_TABS, DEFAULT_GAMMA_VIEW, listedExpirationForView, todayInNewYork } from "./gamma-views";

describe("gamma view tabs", () => {
  it("opens on 0DTE and keeps the combined view as the fourth tab", () => {
    expect(DEFAULT_GAMMA_VIEW).toBe("0dte");
    expect(GAMMA_VIEW_TABS.map((tab) => tab.label)).toEqual(["0DTE", "1DTE", "2DTE", "0-2 DTE"]);
  });

  it("reads expirations by LISTED order from today on, like the backend", () => {
    // A Friday: today's expiration, then Monday and Tuesday (the weekend has none). Past dates are ignored.
    const listed = ["2026-10-29", "2026-10-30", "2026-11-02", "2026-11-03", "2026-11-20"];
    expect(listedExpirationForView(listed, "0dte", "2026-10-30")).toBe("2026-10-30");
    expect(listedExpirationForView(listed, "1dte", "2026-10-30")).toBe("2026-11-02");
    expect(listedExpirationForView(listed, "2dte", "2026-10-30")).toBe("2026-11-03");
    expect(listedExpirationForView(listed, "tactical", "2026-10-30")).toBeNull();
  });

  it("uses the nearest weekly when nothing expires today (NVDA / VIX) and says nothing when the slot does not exist", () => {
    const vix = ["2026-10-28", "2026-11-04"];
    expect(listedExpirationForView(vix, "0dte", "2026-10-26")).toBe("2026-10-28");
    expect(listedExpirationForView(vix, "2dte", "2026-10-26")).toBeNull();
  });

  it("takes the date in New York, not the browser's", () => {
    // 01:30 UTC on the 27th is still the 26th in New York.
    expect(todayInNewYork(new Date("2026-10-27T01:30:00Z"))).toBe("2026-10-26");
  });
});
