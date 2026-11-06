import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithLanguage } from "@/lib/i18n/test-utils";

const getFutureLive = vi.fn();
vi.mock("@/lib/api", () => ({ getFutureLive: (symbol: string, signal?: AbortSignal) => getFutureLive(symbol, signal) }));

import { FuturesLiveBadge } from "./futures-live-badge";

const base = { schema_version: 1, symbol: "ES", offset: 31.2, offset_source: "ninjatrader", live_basis: 31.2, anchor_offset: 26.05, quote: null };

describe("FuturesLiveBadge (ES / NQ basis in use, real price for the owner)", () => {
  afterEach(() => getFutureLive.mockReset());

  it("shows the live basis and, when the API sends it (owner), the real NinjaTrader price", async () => {
    getFutureLive.mockResolvedValue({ ...base, quote: { contract: "ES 12-26", last: 7445.25, bid: 7445, ask: 7445.25, time: "2026-11-06T15:01:02Z", age_seconds: 0.4 } });
    renderWithLanguage(<FuturesLiveBadge symbol="ES" />);
    await waitFor(() => expect(screen.getByText(/ES real 7445,25/)).toBeInTheDocument());
    expect(screen.getByText(/base \+31,20 \(NT\)/)).toBeInTheDocument();
  });

  it("says the anchor is in use when NinjaTrader is not sending, and shows no price for a teammate", async () => {
    getFutureLive.mockResolvedValue({ ...base, offset: 26.05, offset_source: "anchor", live_basis: null });
    renderWithLanguage(<FuturesLiveBadge symbol="ES" />);
    await waitFor(() => expect(screen.getByText(/base \+26,05 \(ancla\)/)).toBeInTheDocument());
    expect(screen.queryByText(/real/)).toBeNull();
  });

  it("asks nothing for a symbol that is not ES / NQ", () => {
    renderWithLanguage(<FuturesLiveBadge symbol="SPX" />);
    expect(getFutureLive).not.toHaveBeenCalled();
  });
});
