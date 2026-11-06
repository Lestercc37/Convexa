import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithLanguage } from "@/lib/i18n/test-utils";

const getPriceSources = vi.fn();
vi.mock("@/lib/api", () => ({ getPriceSources: (signal?: AbortSignal) => getPriceSources(signal) }));

import { PriceSourceBadge, priceIndexFor } from "./price-source-badge";

const replacedSpx = { schema_version: 1, replaced: [{ symbol: "SPX", proxy: "SPY", ratio: 10.0296, reason: "drift", since: "2026-11-06T14:36:13Z" }] };

describe("PriceSourceBadge (index written from its ETF while ThetaData lags)", () => {
  afterEach(() => getPriceSources.mockReset());

  it("maps every chart to the index its price follows", () => {
    expect(priceIndexFor("ES")).toBe("SPX");
    expect(priceIndexFor("spx")).toBe("SPX");
    expect(priceIndexFor("NQ")).toBe("NDX");
    expect(priceIndexFor("SPY")).toBeNull();
  });

  it("shows on SPX and ES while SPX is replaced, with the start time in ET", async () => {
    getPriceSources.mockResolvedValue(replacedSpx);
    const { unmount } = renderWithLanguage(<PriceSourceBadge symbol="ES" />);
    const pill = await screen.findByRole("status");
    expect(pill).toHaveTextContent("SPX ≈ SPY");
    expect(pill.getAttribute("title")).toContain("09:36");
    expect(pill.getAttribute("title")).toContain("10.0296");
    unmount();
  });

  it("stays hidden for NDX when only SPX is replaced, and never asks for a symbol with its own price", async () => {
    getPriceSources.mockResolvedValue(replacedSpx);
    renderWithLanguage(<PriceSourceBadge symbol="NQ" />);
    await waitFor(() => expect(getPriceSources).toHaveBeenCalled());
    expect(screen.queryByRole("status")).toBeNull();
    getPriceSources.mockClear();
    renderWithLanguage(<PriceSourceBadge symbol="SPY" />);
    expect(getPriceSources).not.toHaveBeenCalled();
  });
});
