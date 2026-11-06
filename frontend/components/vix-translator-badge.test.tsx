import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import { translateVixChange, vixBand, vixOpenFrom, vixTargetFor } from "@/lib/vix-translator";
import { VixTranslatorBadge } from "./vix-translator-badge";

vi.mock("@/lib/api", () => ({
  getMarket: vi.fn(),
  getMarketPriceHistory: vi.fn(),
}));

import { getMarket, getMarketPriceHistory } from "@/lib/api";

describe("VIX -> SPX / NDX bands (study 2026-11-05)", () => {
  it("picks the band of the VIX level and maps ES / NQ to their index", () => {
    expect(vixBand("SPX", 15.2)).toEqual({ low: 33, high: 60 });
    expect(vixBand("SPX", 19.4)).toEqual({ low: 22, high: 44 });
    expect(vixBand("SPX", 27)).toEqual({ low: 16, high: 32 });
    expect(vixBand("NDX", 18)).toEqual({ low: 112, high: 267 });
    expect([vixTargetFor("es"), vixTargetFor("NQ"), vixTargetFor("SPY")]).toEqual(["SPX", "NDX", null]);
  });

  it("translates today's VIX change with the band of the opening level, opposite in sign", () => {
    // 2026-11-05: VIX 19.40 at 09:44 -> 26.86 at the close (+7.46), SPX fell 222 from there.
    const move = translateVixChange("SPX", 19.4, 26.86);
    expect(move?.from).toBeCloseTo(-164.1, 1);
    expect(move?.to).toBeCloseTo(-328.2, 1);
    expect(translateVixChange("NDX", 20, 19)).toEqual({ from: 75, to: 164 });
    expect(translateVixChange("SPX", 18, 18)).toBeNull();
  });

  it("takes the open from today's first reading at or after 09:31 ET", () => {
    const points = [
      { timestamp: "2026-11-04T20:59:00Z", price: 17.2 }, // yesterday
      { timestamp: "2026-11-05T14:30:10Z", price: 17.2 }, // 09:30 print = yesterday's close
      { timestamp: "2026-11-05T14:31:05Z", price: 19.1 },
      { timestamp: "2026-11-05T15:00:00Z", price: 19.4 },
    ];
    expect(vixOpenFrom(points, new Date("2026-11-05T18:00:00Z"))).toBe(19.1);
    expect(vixOpenFrom(points, new Date("2026-11-06T14:00:00Z"))).toBeNull();
  });
});

describe("VixTranslatorBadge", () => {
  beforeEach(() => {
    vi.mocked(getMarket).mockReset();
    vi.mocked(getMarketPriceHistory).mockReset();
  });

  it("shows the VIX, the points per VIX point and today's change translated for SPX", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-05T20:00:00Z"));
    vi.mocked(getMarket).mockResolvedValue({ price: 26.86 } as never);
    vi.mocked(getMarketPriceHistory).mockResolvedValue({ points: [{ timestamp: "2026-11-05T14:31:00Z", price: 19.4 }] } as never);
    renderWithLanguage(<VixTranslatorBadge symbol="SPX" />);
    const pill = await screen.findByText(/VIX 26\.86/);
    expect(pill).toHaveTextContent("+1 ≈ SPX −16 a −32");
    await waitFor(() => expect(pill).toHaveTextContent("hoy +7.46 ≈ −164 a −328"));
    expect(getMarket).toHaveBeenCalledWith("VIX", expect.anything());
    vi.useRealTimers();
  });

  it("is not shown for symbols the study did not measure", () => {
    const { container } = renderWithLanguage(<VixTranslatorBadge symbol="SPY" />);
    expect(container).toBeEmptyDOMElement();
    expect(getMarket).not.toHaveBeenCalled();
  });
});
