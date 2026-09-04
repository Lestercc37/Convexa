import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import { QuickScreener } from "./quick-screener";

const apiMocks = vi.hoisted(() => ({
  getScreenerPreset: vi.fn(),
  getScreenerPresetSettings: vi.fn(),
}));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, ...apiMocks };
});

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.getScreenerPreset.mockImplementation((preset: string) =>
    Promise.resolve({
      schema_version: 1,
      preset,
      results:
        preset === "negative-gamma-board"
          ? [
              {
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
              },
            ]
          : preset === "unusual-options-activity"
            ? [
                {
                  symbol: "SPY",
                  as_of: "2026-08-06T14:30:00Z",
                  contract: "SPY260220C00540000",
                  alert_type: "WHALE",
                  amount: 210000,
                  net_gamma: null,
                  gamma_flip: null,
                  call_wall: null,
                  put_wall: null,
                  max_pain: null,
                  vanna_exposure: null,
                  charm_exposure: null,
                },
              ]
            : [],
    }),
  );
});

describe("QuickScreener", () => {
  it("loads the selected preset and renders its table", async () => {
    const user = userEvent.setup();
    renderWithLanguage(<QuickScreener />);

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

  it("renders alert_type through the same Whale/Unusual labels as AlertsPanel, not the raw enum", async () => {
    // Additional fix folded into the i18n PR: this used to render the raw
    // backend enum ("WHALE") directly instead of reusing alerts-panel.tsx's
    // TYPE_LABEL map.
    renderWithLanguage(<QuickScreener />);

    await screen.findByText("SPY");
    expect(screen.getByText("Whale")).toBeInTheDocument();
    expect(screen.queryByText("WHALE")).not.toBeInTheDocument();
  });

  it("keys two distinct alerts on the same contract by alert_type too, not just symbol+contract (regression)", async () => {
    // Confirmed live, 2026-09: a single reading can independently trip a
    // magnitude threshold (WHALE/UNUSUAL) *and* the separate sustained-
    // flow window, so the real API can return the same symbol+contract+
    // as_of twice with a different alert_type/amount -- e.g. real traffic
    // captured QQQ260904C00722000 as both SUSTAINED_FLOW (625,220.5) and
    // UNUSUAL (274,384.5) at the identical microsecond as_of. Before this
    // fix, the row key was only `${symbol}-${contract ?? as_of}`, so React
    // saw a duplicate key and both `results.map` rows fought over the same
    // DOM node -- assert here that both rows render distinctly instead.
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    apiMocks.getScreenerPreset.mockImplementation((preset: string) =>
      Promise.resolve({
        schema_version: 1,
        preset,
        results:
          preset === "unusual-options-activity"
            ? [
                {
                  symbol: "QQQ",
                  as_of: "2026-09-04T13:59:12.058844Z",
                  contract: "QQQ260904C00722000",
                  alert_type: "SUSTAINED_FLOW",
                  amount: 625220.5,
                  net_gamma: null,
                  gamma_flip: null,
                  call_wall: null,
                  put_wall: null,
                  max_pain: null,
                  vanna_exposure: null,
                  charm_exposure: null,
                },
                {
                  symbol: "QQQ",
                  as_of: "2026-09-04T13:59:12.058844Z",
                  contract: "QQQ260904C00722000",
                  alert_type: "UNUSUAL",
                  amount: 274384.5,
                  net_gamma: null,
                  gamma_flip: null,
                  call_wall: null,
                  put_wall: null,
                  max_pain: null,
                  vanna_exposure: null,
                  charm_exposure: null,
                },
              ]
            : [],
      }),
    );

    // Default preset on mount is already "unusual-options-activity"
    // (PRESETS[0]) -- no selectOptions needed to trigger the fetch.
    renderWithLanguage(<QuickScreener />);

    expect(await screen.findByText("Sustained Flow")).toBeInTheDocument();
    expect(screen.getByText("Unusual")).toBeInTheDocument();
    expect(screen.getByText("$625,221")).toBeInTheDocument();
    expect(screen.getByText("$274,385")).toBeInTheDocument();
    expect(
      consoleError.mock.calls.some((call) => String(call[0]).includes("same key")),
    ).toBe(false);

    consoleError.mockRestore();
  });

  it("keys two UNUSUAL alerts on the same contract by as_of too, not just symbol+contract (regression)", async () => {
    // Confirmed live against the real running backend, 2026-09: the same
    // contract legitimately racks up more than one UNUSUAL alert across
    // the session as volume keeps flowing -- each a real, separate
    // WhaleAlert with its own as_of. Before this fix, the row key dropped
    // `as_of` entirely whenever `contract` was present (`item.contract ??
    // item.as_of`), so two alerts on the same contract collided
    // regardless of when they actually happened -- caught live as a
    // React "duplicate key" console error for QQQ260904C00721000 with two
    // different UNUSUAL rows.
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    apiMocks.getScreenerPreset.mockImplementation((preset: string) =>
      Promise.resolve({
        schema_version: 1,
        preset,
        results:
          preset === "unusual-options-activity"
            ? [
                {
                  symbol: "QQQ",
                  as_of: "2026-09-04T13:40:00.000000Z",
                  contract: "QQQ260904C00721000",
                  alert_type: "UNUSUAL",
                  amount: 92173.0,
                  net_gamma: null,
                  gamma_flip: null,
                  call_wall: null,
                  put_wall: null,
                  max_pain: null,
                  vanna_exposure: null,
                  charm_exposure: null,
                },
                {
                  symbol: "QQQ",
                  as_of: "2026-09-04T13:55:00.000000Z",
                  contract: "QQQ260904C00721000",
                  alert_type: "UNUSUAL",
                  amount: 118420.0,
                  net_gamma: null,
                  gamma_flip: null,
                  call_wall: null,
                  put_wall: null,
                  max_pain: null,
                  vanna_exposure: null,
                  charm_exposure: null,
                },
              ]
            : [],
      }),
    );

    renderWithLanguage(<QuickScreener />);

    expect(await screen.findByText("$92,173")).toBeInTheDocument();
    expect(screen.getByText("$118,420")).toBeInTheDocument();
    expect(
      consoleError.mock.calls.some((call) => String(call[0]).includes("same key")),
    ).toBe(false);

    consoleError.mockRestore();
  });

  it("refreshes the active preset automatically every 30s, not only when the dropdown changes (regression)", async () => {
    // Before this fix, QuickScreener only fetched on mount and on preset
    // change -- unlike every other polling panel in the dashboard
    // (AlertsPanel, ChartSecondaryPanel), it never refreshed on its own,
    // so "Unusual Options Activity" (and every other preset) could go
    // stale indefinitely while left open.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderWithLanguage(<QuickScreener />);

    await vi.waitFor(() => expect(apiMocks.getScreenerPreset).toHaveBeenCalledTimes(1));

    await vi.advanceTimersByTimeAsync(30_000);
    await vi.waitFor(() => expect(apiMocks.getScreenerPreset).toHaveBeenCalledTimes(2));

    await vi.advanceTimersByTimeAsync(30_000);
    await vi.waitFor(() => expect(apiMocks.getScreenerPreset).toHaveBeenCalledTimes(3));

    expect(apiMocks.getScreenerPreset).toHaveBeenLastCalledWith(
      "unusual-options-activity",
      expect.any(AbortSignal),
    );

    vi.useRealTimers();
  });

  it("opens the preset filter settings panel from its trigger button", async () => {
    apiMocks.getScreenerPresetSettings.mockResolvedValue({
      schema_version: 1,
      settings: [
        { preset: "negative-gamma-board", net_gamma_max: 0, min_magnitude: null, limit: null },
        {
          preset: "vanna-exposure-leaders",
          net_gamma_max: null,
          min_magnitude: null,
          limit: null,
        },
        {
          preset: "charm-decay-pressure",
          net_gamma_max: null,
          min_magnitude: null,
          limit: null,
        },
      ],
    });
    const user = userEvent.setup();
    renderWithLanguage(<QuickScreener />);

    expect(screen.queryByText("Configuración de Filtros de Presets")).not.toBeInTheDocument();
    await user.click(screen.getByLabelText("Configuración de filtros de presets"));

    expect(await screen.findByText("Configuración de Filtros de Presets")).toBeInTheDocument();
    await waitFor(() => expect(apiMocks.getScreenerPresetSettings).toHaveBeenCalled());
  });
});
