import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import type { FutureOpeningPriceResponse } from "@/lib/types";
import { FutureOpeningPriceControl } from "./future-opening-price-control";

const apiMocks = vi.hoisted(() => ({
  getFutureOpeningPrice: vi.fn(),
  setFutureOpeningPrice: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, ...apiMocks };
});

import { ApiError } from "@/lib/api";

function response(overrides: Partial<FutureOpeningPriceResponse> = {}): FutureOpeningPriceResponse {
  return {
    schema_version: 1,
    symbol: "ES",
    proxy_symbol: "SPX",
    session_date: "2026-10-13",
    opening_price: null,
    saved_at: null,
    accepting: true,
    waiting_reason: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.getFutureOpeningPrice.mockResolvedValue(response());
});

describe("FutureOpeningPriceControl", () => {
  it("says what number to type: the 9:30:00 open of the 1-minute candle, not the current price", async () => {
    renderWithLanguage(<FutureOpeningPriceControl symbol="ES" onSaved={() => {}} />);

    expect(
      await screen.findByText(
        "Precio de /ES a las 9:30:00, el open de la vela de 1 minuto, no el precio actual",
      ),
    ).toBeInTheDocument();
  });

  it("uses the symbol's own future name for NQ", async () => {
    apiMocks.getFutureOpeningPrice.mockResolvedValue(response({ symbol: "NQ", proxy_symbol: "NDX" }));
    renderWithLanguage(<FutureOpeningPriceControl symbol="NQ" onSaved={() => {}} />);

    expect(await screen.findByText(/Precio de \/NQ a las 9:30:00/)).toBeInTheDocument();
  });

  it("before the open: nothing from yesterday is shown, saving is disabled and it says when it opens", async () => {
    apiMocks.getFutureOpeningPrice.mockResolvedValue(
      response({ accepting: false, waiting_reason: "before_open" }),
    );
    const user = userEvent.setup();
    renderWithLanguage(<FutureOpeningPriceControl symbol="ES" onSaved={() => {}} />);

    expect(
      await screen.findByText("Se puede guardar desde las 9:30:02 ET, cuando SPX tenga su primer precio de hoy"),
    ).toBeInTheDocument();
    const input = screen.getByPlaceholderText("Apertura 9:30 ET");
    expect(input).toHaveValue(null);
    await user.type(input, "7826");
    expect(screen.getByRole("button", { name: "Guardar" })).toBeDisabled();
  });

  it("between 9:30 and the index's first price it asks to wait for that price", async () => {
    apiMocks.getFutureOpeningPrice.mockResolvedValue(
      response({ accepting: false, waiting_reason: "waiting_first_price" }),
    );
    renderWithLanguage(<FutureOpeningPriceControl symbol="ES" onSaved={() => {}} />);

    expect(
      await screen.findByText("Esperando el primer precio de SPX de hoy (9:30:02 ET)"),
    ).toBeInTheDocument();
  });

  it("shows which session the stored number belongs to and when it was saved (ET)", async () => {
    apiMocks.getFutureOpeningPrice.mockResolvedValue(
      response({ opening_price: 7826, saved_at: "2026-10-13T13:31:10Z" }),
    );
    renderWithLanguage(<FutureOpeningPriceControl symbol="ES" onSaved={() => {}} />);

    expect(
      await screen.findByText("Guardado para la sesión del 2026-10-13: 7826 (09:31:10 ET)"),
    ).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Apertura 9:30 ET")).toHaveValue(7826);
  });

  it("with no number yet it names the session it is waiting for", async () => {
    renderWithLanguage(<FutureOpeningPriceControl symbol="ES" onSaved={() => {}} />);

    expect(
      await screen.findByText("Sin precio guardado para la sesión del 2026-10-13"),
    ).toBeInTheDocument();
  });

  it("saving sends the number, tells the dashboard and shows the session it was saved for", async () => {
    apiMocks.setFutureOpeningPrice.mockResolvedValue(
      response({ opening_price: 7826, saved_at: "2026-10-13T13:31:10Z" }),
    );
    const onSaved = vi.fn();
    const user = userEvent.setup();
    renderWithLanguage(<FutureOpeningPriceControl symbol="ES" onSaved={onSaved} />);
    await screen.findByText(/Precio de \/ES a las 9:30:00/);

    await user.type(screen.getByPlaceholderText("Apertura 9:30 ET"), "7826");
    await user.click(screen.getByRole("button", { name: "Guardar" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(apiMocks.setFutureOpeningPrice).toHaveBeenCalledWith("ES", 7826);
    expect(
      await screen.findByText("Guardado para la sesión del 2026-10-13: 7826 (09:31:10 ET)"),
    ).toBeInTheDocument();
  });

  it("if the server refuses because the session has not started, it explains the wait (not a generic error)", async () => {
    apiMocks.setFutureOpeningPrice.mockRejectedValue(new ApiError(409, "OPENING_PRICE_NOT_OPEN_YET"));
    const onSaved = vi.fn();
    const user = userEvent.setup();
    renderWithLanguage(<FutureOpeningPriceControl symbol="ES" onSaved={onSaved} />);
    await screen.findByText(/Precio de \/ES a las 9:30:00/);

    await user.type(screen.getByPlaceholderText("Apertura 9:30 ET"), "7826");
    await user.click(screen.getByRole("button", { name: "Guardar" }));

    expect(
      await screen.findByText("Esperando el primer precio de SPX de hoy (9:30:02 ET)"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("any other save failure shows the generic error", async () => {
    apiMocks.setFutureOpeningPrice.mockRejectedValue(new ApiError(500));
    const user = userEvent.setup();
    renderWithLanguage(<FutureOpeningPriceControl symbol="ES" onSaved={() => {}} />);
    await screen.findByText(/Precio de \/ES a las 9:30:00/);

    await user.type(screen.getByPlaceholderText("Apertura 9:30 ET"), "7826");
    await user.click(screen.getByRole("button", { name: "Guardar" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo guardar el precio de apertura.");
  });
});
