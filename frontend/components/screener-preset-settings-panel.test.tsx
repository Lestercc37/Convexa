import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import { ScreenerPresetSettingsPanel } from "./screener-preset-settings-panel";

const apiMocks = vi.hoisted(() => ({
  getScreenerPresetSettings: vi.fn(),
  updateScreenerPresetSettings: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, ...apiMocks };
});

function settingsResponse() {
  return {
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
        min_magnitude: 500,
        limit: 10,
      },
    ],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.getScreenerPresetSettings.mockResolvedValue(settingsResponse());
});

function rowFor(label: string): HTMLElement {
  const cell = screen.getByText(label, { selector: "td.whale-thresholds-symbol" });
  const row = cell.closest("tr");
  if (!row) throw new Error(`No row found for ${label}`);
  return row;
}

describe("ScreenerPresetSettingsPanel", () => {
  it("loads and renders one row per configurable preset with its current values", async () => {
    renderWithLanguage(<ScreenerPresetSettingsPanel onClose={vi.fn()} />);

    expect(await screen.findByText("Negative Gamma Board")).toBeInTheDocument();
    expect(screen.getByText("Vanna Exposure Leaders")).toBeInTheDocument();
    expect(screen.getByText("Charm Decay Pressure")).toBeInTheDocument();
    expect(screen.getByLabelText("Negative Gamma Board Net Gamma Max")).toHaveValue(0);
    expect(screen.getByLabelText("Charm Decay Pressure Magnitud Mín.")).toHaveValue(500);
    expect(screen.getByLabelText("Charm Decay Pressure Límite (top-N)")).toHaveValue(10);
  });

  it("shows a dash for the fields that don't apply to each preset", async () => {
    renderWithLanguage(<ScreenerPresetSettingsPanel onClose={vi.fn()} />);
    await screen.findByText("Negative Gamma Board");

    const gammaRow = rowFor("Negative Gamma Board");
    expect(within(gammaRow).getAllByText("—")).toHaveLength(2);
    const vannaRow = rowFor("Vanna Exposure Leaders");
    expect(within(vannaRow).getByText("—")).toBeInTheDocument();
  });

  it("edits and saves Negative Gamma Board's net_gamma_max, sending only that field", async () => {
    apiMocks.updateScreenerPresetSettings.mockResolvedValue({
      preset: "negative-gamma-board",
      net_gamma_max: -50,
      min_magnitude: null,
      limit: null,
    });
    const user = userEvent.setup();
    renderWithLanguage(<ScreenerPresetSettingsPanel onClose={vi.fn()} />);
    await screen.findByText("Negative Gamma Board");

    const input = screen.getByLabelText("Negative Gamma Board Net Gamma Max");
    await user.clear(input);
    await user.type(input, "-50");
    await user.click(
      within(rowFor("Negative Gamma Board")).getByRole("button", { name: "Guardar" }),
    );

    await waitFor(() =>
      expect(apiMocks.updateScreenerPresetSettings).toHaveBeenCalledWith(
        "negative-gamma-board",
        { net_gamma_max: -50 },
      ),
    );
    expect(
      await within(rowFor("Negative Gamma Board")).findByText("Guardado"),
    ).toBeInTheDocument();
  });

  it("edits and saves Vanna Exposure Leaders' both fields together, sending nulls for blanks", async () => {
    apiMocks.updateScreenerPresetSettings.mockResolvedValue({
      preset: "vanna-exposure-leaders",
      net_gamma_max: null,
      min_magnitude: null,
      limit: 5,
    });
    const user = userEvent.setup();
    renderWithLanguage(<ScreenerPresetSettingsPanel onClose={vi.fn()} />);
    await screen.findByText("Vanna Exposure Leaders");

    const limitInput = screen.getByLabelText("Vanna Exposure Leaders Límite (top-N)");
    await user.clear(limitInput);
    await user.type(limitInput, "5");
    await user.click(
      within(rowFor("Vanna Exposure Leaders")).getByRole("button", { name: "Guardar" }),
    );

    await waitFor(() =>
      expect(apiMocks.updateScreenerPresetSettings).toHaveBeenCalledWith(
        "vanna-exposure-leaders",
        { min_magnitude: null, limit: 5 },
      ),
    );
  });

  it("rejects a negative min_magnitude client-side, without calling the endpoint", async () => {
    const user = userEvent.setup();
    renderWithLanguage(<ScreenerPresetSettingsPanel onClose={vi.fn()} />);
    await screen.findByText("Charm Decay Pressure");

    const minMagnitudeInput = screen.getByLabelText("Charm Decay Pressure Magnitud Mín.");
    await user.clear(minMagnitudeInput);
    await user.type(minMagnitudeInput, "-1");
    await user.click(
      within(rowFor("Charm Decay Pressure")).getByRole("button", { name: "Guardar" }),
    );

    expect(
      await within(rowFor("Charm Decay Pressure")).findByText(
        "Magnitud mín. debe ser un número no negativo (o vacío) y el límite un entero positivo (o vacío)",
      ),
    ).toBeInTheDocument();
    expect(apiMocks.updateScreenerPresetSettings).not.toHaveBeenCalled();
  });

  it("rejects a non-finite net_gamma_max client-side, without calling the endpoint", async () => {
    const user = userEvent.setup();
    renderWithLanguage(<ScreenerPresetSettingsPanel onClose={vi.fn()} />);
    await screen.findByText("Negative Gamma Board");

    const input = screen.getByLabelText("Negative Gamma Board Net Gamma Max");
    await user.clear(input);
    await user.click(
      within(rowFor("Negative Gamma Board")).getByRole("button", { name: "Guardar" }),
    );

    expect(
      await within(rowFor("Negative Gamma Board")).findByText(
        "Net Gamma Max debe ser un número",
      ),
    ).toBeInTheDocument();
    expect(apiMocks.updateScreenerPresetSettings).not.toHaveBeenCalled();
  });

  it("shows a translated error when saving fails on the server", async () => {
    apiMocks.updateScreenerPresetSettings.mockRejectedValue(new ApiError(422));
    const user = userEvent.setup();
    renderWithLanguage(<ScreenerPresetSettingsPanel onClose={vi.fn()} />);
    await screen.findByText("Negative Gamma Board");

    const saveButtons = await screen.findAllByRole("button", { name: "Guardar" });
    await user.click(saveButtons[0]);

    expect(
      await screen.findByText("No se pudo completar la solicitud. Intenta de nuevo."),
    ).toBeInTheDocument();
  });

  it("shows a translated error when the initial load fails", async () => {
    apiMocks.getScreenerPresetSettings.mockRejectedValue(new ApiError(500));
    renderWithLanguage(<ScreenerPresetSettingsPanel onClose={vi.fn()} />);

    expect(
      await screen.findByText("No se pudo completar la solicitud. Intenta de nuevo."),
    ).toBeInTheDocument();
  });

  it("closes via the close button, overlay click, and Escape", async () => {
    const user = userEvent.setup();
    const onCloseButton = vi.fn();
    const { unmount } = renderWithLanguage(
      <ScreenerPresetSettingsPanel onClose={onCloseButton} />,
    );
    await screen.findByText("Negative Gamma Board");

    await user.click(screen.getByRole("button", { name: "Cerrar" }));
    expect(onCloseButton).toHaveBeenCalledTimes(1);
    unmount();

    const onCloseOverlay = vi.fn();
    const { container, unmount: unmountOverlay } = renderWithLanguage(
      <ScreenerPresetSettingsPanel onClose={onCloseOverlay} />,
    );
    await screen.findByText("Negative Gamma Board");
    const overlay = container.querySelector(".modal-overlay");
    expect(overlay).not.toBeNull();
    if (overlay) await user.click(overlay);
    expect(onCloseOverlay).toHaveBeenCalledTimes(1);
    unmountOverlay();

    const onCloseEscape = vi.fn();
    renderWithLanguage(<ScreenerPresetSettingsPanel onClose={onCloseEscape} />);
    await screen.findByText("Negative Gamma Board");
    await user.keyboard("{Escape}");
    expect(onCloseEscape).toHaveBeenCalledTimes(1);
  });
});
