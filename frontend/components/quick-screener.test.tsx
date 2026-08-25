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
