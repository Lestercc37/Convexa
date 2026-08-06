import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QuickScreener } from "./quick-screener";

const apiMocks = vi.hoisted(() => ({ getScreenerPreset: vi.fn() }));
vi.mock("@/lib/api", () => apiMocks);

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.getScreenerPreset.mockImplementation((preset: string) =>
    Promise.resolve({
      schema_version: 1,
      preset,
      results: preset === "negative-gamma-board" ? [{
        symbol: "SPX",
        as_of: "2026-08-06T14:30:00Z",
        contract: null,
        alert_type: null,
        amount: null,
        net_gamma: -80000,
        gamma_flip: null,
        call_wall: null,
        put_wall: null,
        max_pain: null,
        vanna_exposure: null,
        charm_exposure: null,
      }] : [],
    }),
  );
});

describe("QuickScreener", () => {
  it("loads the selected preset and renders its table", async () => {
    const user = userEvent.setup();
    render(<QuickScreener />);

    await user.selectOptions(screen.getByLabelText("Preset"), "negative-gamma-board");

    await screen.findByText("SPX");
    expect(screen.getByText("-80,000")).toBeInTheDocument();
    await waitFor(() =>
      expect(apiMocks.getScreenerPreset).toHaveBeenLastCalledWith(
        "negative-gamma-board",
        expect.any(AbortSignal),
      ),
    );
  });
});
