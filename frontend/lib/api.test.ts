import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  getGamma,
  getGammaHeatmap,
  getGammaHistory,
  getGammaProfile,
  getMarket,
  getOptionChain,
  getOptionChainExpirations,
  getUnderlyings,
  historyWindowStart,
  setFutureOpeningPrice,
} from "./api";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("getGammaHeatmap", () => {
  it("sends the view, metric, start and the column size in seconds", async () => {
    fetchMock.mockResolvedValue(response({}));

    await getGammaHeatmap("SPX", { view: "tactical", metric: "gamma", start: new Date("2026-10-16T12:30:00Z"), bucketSeconds: 60 });
    await getGammaHeatmap("SPX", {});

    const first = String(fetchMock.mock.calls[0][0]);
    expect(first).toContain("/gamma/SPX/heatmap?");
    expect(first).toContain("view=tactical");
    expect(first).toContain("metric=gamma");
    expect(first).toContain("start=2026-10-16T12%3A30%3A00.000Z");
    expect(first).toContain("bucket_seconds=60");
    expect(String(fetchMock.mock.calls[1][0])).not.toContain("bucket_seconds"); // omitted: the server default
  });
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

  it("requests only the expiration list from its own lightweight route, not the full chain", async () => {
    fetchMock.mockResolvedValueOnce(response({ symbol: "SPY", expirations: ["2026-01-16"] }));

    await getOptionChainExpirations("SPY");

    expect(fetchMock).toHaveBeenCalledWith(
      "/backend/api/v1/chain/SPY/expirations",
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

describe("ApiError code", () => {
  it("carries the API's own error code when the failed response has one", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: async () => ({ error: { code: "OPENING_PRICE_NOT_OPEN_YET", message: "wait" } }),
    });
    await expect(setFutureOpeningPrice("ES", 7826)).rejects.toMatchObject({
      status: 409,
      code: "OPENING_PRICE_NOT_OPEN_YET",
    });
  });

  it("has no code when the failed response is not JSON", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 502,
      json: async () => {
        throw new Error("not json");
      },
    });
    await expect(setFutureOpeningPrice("ES", 7826)).rejects.toMatchObject({ status: 502, code: null });
  });
});

describe("Historical level window", () => {
  it("starts five weekdays plus one day back, skipping weekends", () => {
    // Fri 2026-10-16 -> back over Thu, Wed, Tue, Mon, then Fri 10-09 is the 5th weekday; one more day back = Thu 10-08.
    expect(historyWindowStart(new Date("2026-10-16T18:00:00Z")).toISOString()).toBe("2026-10-08T00:00:00.000Z");
    // Mon 2026-10-19 -> Fri, Thu, Wed, Tue, Mon (10-12) is the 5th weekday; one more day back = Sun 10-11.
    expect(historyWindowStart(new Date("2026-10-19T13:00:00Z")).toISOString()).toBe("2026-10-11T00:00:00.000Z");
  });

  it("asks the endpoint for a bounded range instead of the whole history", async () => {
    fetchMock.mockResolvedValue(response({ schema_version: 1, symbol: "SPY", items: [] }));
    await getGammaHistory("SPY", "tactical");
    const [url] = fetchMock.mock.calls[0];
    const parsed = new URL(String(url), "http://localhost");
    expect(parsed.pathname.endsWith("/gamma/SPY/history")).toBe(true);
    expect(parsed.searchParams.get("view")).toBe("tactical");
    expect(parsed.searchParams.get("start")).toBe(historyWindowStart().toISOString());
  });
});
