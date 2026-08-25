import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import { WhaleThresholdsPanel } from "./whale-thresholds-panel";

const apiMocks = vi.hoisted(() => ({
  getWhaleThresholds: vi.fn(),
  updateWhaleThreshold: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, ...apiMocks };
});

function thresholdsResponse() {
  return {
    schema_version: 1,
    thresholds: [
      {
        symbol: "SPY",
        unusual_min: 40000,
        whale_min: 150000,
        unusual_multiplier: 3.0,
        whale_multiplier: 6.0,
        sustained_flow_min: 500000,
      },
      {
        symbol: "QQQ",
        unusual_min: 40000,
        whale_min: 150000,
        unusual_multiplier: 3.0,
        whale_multiplier: 6.0,
        sustained_flow_min: 500000,
      },
    ],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.getWhaleThresholds.mockResolvedValue(thresholdsResponse());
});

// Rows sort alphabetically (QQQ before SPY) — scope to the right <tr>
// instead of assuming save-button order matches fixture order.
function rowFor(symbol: string): HTMLElement {
  const cell = screen.getByText(symbol, { selector: "td.whale-thresholds-symbol" });
  const row = cell.closest("tr");
  if (!row) throw new Error(`No row found for ${symbol}`);
  return row;
}

describe("WhaleThresholdsPanel", () => {
  it("loads and renders one row per symbol with its current values", async () => {
    renderWithLanguage(<WhaleThresholdsPanel onClose={vi.fn()} />);

    expect(await screen.findByText("SPY")).toBeInTheDocument();
    expect(screen.getByText("QQQ")).toBeInTheDocument();
    expect(screen.getByLabelText("SPY Unusual mín. ($)")).toHaveValue(40000);
    expect(screen.getByLabelText("QQQ Flujo Sostenido mín. ($)")).toHaveValue(500000);
  });

  it("edits a field and saves it, showing a confirmation, calling the endpoint with the full updated set", async () => {
    apiMocks.updateWhaleThreshold.mockResolvedValue({
      symbol: "SPY",
      unusual_min: 50000,
      whale_min: 150000,
      unusual_multiplier: 3.0,
      whale_multiplier: 6.0,
      sustained_flow_min: 500000,
    });
    const user = userEvent.setup();
    renderWithLanguage(<WhaleThresholdsPanel onClose={vi.fn()} />);
    await screen.findByText("SPY");

    const unusualMinInput = screen.getByLabelText("SPY Unusual mín. ($)");
    await user.clear(unusualMinInput);
    await user.type(unusualMinInput, "50000");

    await user.click(within(rowFor("SPY")).getByRole("button", { name: "Guardar" }));

    await waitFor(() =>
      expect(apiMocks.updateWhaleThreshold).toHaveBeenCalledWith("SPY", {
        unusual_min: 50000,
        whale_min: 150000,
        unusual_multiplier: 3,
        whale_multiplier: 6,
        sustained_flow_min: 500000,
      }),
    );
    expect(await within(rowFor("SPY")).findByText("Guardado")).toBeInTheDocument();
  });

  it("rejects a non-positive value client-side, without calling the endpoint", async () => {
    const user = userEvent.setup();
    renderWithLanguage(<WhaleThresholdsPanel onClose={vi.fn()} />);
    await screen.findByText("SPY");

    const whaleMinInput = screen.getByLabelText("SPY Whale mín. ($)");
    await user.clear(whaleMinInput);
    await user.type(whaleMinInput, "0");

    await user.click(within(rowFor("SPY")).getByRole("button", { name: "Guardar" }));

    expect(
      await within(rowFor("SPY")).findByText("Los 5 campos deben ser números positivos"),
    ).toBeInTheDocument();
    expect(apiMocks.updateWhaleThreshold).not.toHaveBeenCalled();
  });

  it("shows a translated error when saving fails on the server", async () => {
    apiMocks.updateWhaleThreshold.mockRejectedValue(new ApiError(422));
    const user = userEvent.setup();
    renderWithLanguage(<WhaleThresholdsPanel onClose={vi.fn()} />);
    await screen.findByText("SPY");

    const saveButtons = await screen.findAllByRole("button", { name: "Guardar" });
    await user.click(saveButtons[0]);

    expect(
      await screen.findByText("No se pudo completar la solicitud. Intenta de nuevo."),
    ).toBeInTheDocument();
  });

  it("shows a translated error when the initial load fails", async () => {
    apiMocks.getWhaleThresholds.mockRejectedValue(new ApiError(500));
    renderWithLanguage(<WhaleThresholdsPanel onClose={vi.fn()} />);

    expect(
      await screen.findByText("No se pudo completar la solicitud. Intenta de nuevo."),
    ).toBeInTheDocument();
  });

  it("closes via the close button, overlay click, and Escape", async () => {
    const user = userEvent.setup();
    const onCloseButton = vi.fn();
    const { unmount } = renderWithLanguage(<WhaleThresholdsPanel onClose={onCloseButton} />);
    await screen.findByText("SPY");

    await user.click(screen.getByRole("button", { name: "Cerrar" }));
    expect(onCloseButton).toHaveBeenCalledTimes(1);
    unmount();

    const onCloseOverlay = vi.fn();
    const { container, unmount: unmountOverlay } = renderWithLanguage(
      <WhaleThresholdsPanel onClose={onCloseOverlay} />,
    );
    await screen.findByText("SPY");
    const overlay = container.querySelector(".modal-overlay");
    expect(overlay).not.toBeNull();
    if (overlay) await user.click(overlay);
    expect(onCloseOverlay).toHaveBeenCalledTimes(1);
    unmountOverlay();

    const onCloseEscape = vi.fn();
    renderWithLanguage(<WhaleThresholdsPanel onClose={onCloseEscape} />);
    await screen.findByText("SPY");
    await user.keyboard("{Escape}");
    expect(onCloseEscape).toHaveBeenCalledTimes(1);
  });

  it("clicking inside the modal itself does not close it", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderWithLanguage(<WhaleThresholdsPanel onClose={onClose} />);
    await screen.findByText("SPY");

    await user.click(screen.getByText("Umbrales de Whale Alerts"));
    expect(onClose).not.toHaveBeenCalled();
  });
});
