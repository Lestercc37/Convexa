import { beforeEach, describe, expect, it, vi } from "vitest";
import { getGamma, getGammaProfile, getMarket, getOptionChain, getUnderlyings } from "./api";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("API client cache policy", () => {
  it("disables caching for every Dashboard endpoint", async () => {
    fetchMock.mockResolvedValue(response({}));

    await getUnderlyings();
    await getGamma("SPY");
    await getGammaProfile("SPY");
    await getMarket("SPY");
    await getOptionChain("SPY");

    expect(fetchMock).toHaveBeenCalledTimes(5);
    for (const [, init] of fetchMock.mock.calls) {
      expect(init).toEqual(expect.objectContaining({ cache: "no-store" }));
    }
  });

  it("returns fresh data from two consecutive market requests", async () => {
    fetchMock
      .mockResolvedValueOnce(response({ symbol: "SPY", price: 550 }))
      .mockResolvedValueOnce(response({ symbol: "SPY", price: 551.25 }));

    const first = await getMarket("SPY");
    const second = await getMarket("SPY");

    expect(first.price).toBe(550);
    expect(second.price).toBe(551.25);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/backend/api/v1/market/SPY",
      expect.objectContaining({ cache: "no-store" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/backend/api/v1/market/SPY",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("requests the frozen gamma profile snapshot from its own read-only route", async () => {
    fetchMock.mockResolvedValueOnce(response({ symbol: "SPY" }));

    await getGammaProfile("SPY");

    expect(fetchMock).toHaveBeenCalledWith(
      "/backend/api/v1/gamma/SPY/profile",
      expect.objectContaining({ cache: "no-store" }),
    );
  });
});

function response(payload: object): Response {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
  } as Response;
}
