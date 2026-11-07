import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getEmaAlerts: vi.fn(),
  addEmaAlert: vi.fn(),
  deleteEmaAlert: vi.fn(),
  newTelegramLinkCode: vi.fn(),
}));
vi.mock("@/lib/api", () => api);

import { LanguageProvider } from "@/lib/i18n/language-context";
import { EmaTelegramAlerts } from "./ema-telegram-alerts";

function show(symbol = "NQ", timeframe = "5m") {
  return render(
    <LanguageProvider>
      <EmaTelegramAlerts symbol={symbol} timeframe={timeframe} />
    </LanguageProvider>,
  );
}

describe("EMA cross alerts by Telegram (EMAs menu)", () => {
  beforeEach(() => {
    for (const fn of Object.values(api)) fn.mockReset();
    window.localStorage.setItem("convexa.language", "es");
  });

  it("switches the 8/20 alert on for the chart's symbol and timeframe, and off again", async () => {
    api.getEmaAlerts.mockResolvedValueOnce({ telegram_linked: true, bot_username: "convexa_alertas_bot", rules: [] });
    api.getEmaAlerts.mockResolvedValue({
      telegram_linked: true,
      bot_username: "convexa_alertas_bot",
      rules: [{ id: 3, symbol: "NQ", timeframe: "5m", fast_period: 8, slow_period: 20, direction: "both" }],
    });
    api.addEmaAlert.mockResolvedValue({ id: 3 });
    api.deleteEmaAlert.mockResolvedValue(undefined);
    show();
    const toggle = await screen.findByRole("checkbox");
    await waitFor(() => expect(toggle).not.toBeDisabled());
    await act(async () => fireEvent.click(toggle));
    expect(api.addEmaAlert).toHaveBeenCalledWith({ symbol: "NQ", timeframe: "5m", fast_period: 8, slow_period: 20 });
    await waitFor(() => expect(screen.getByRole("checkbox")).toBeChecked());
    await act(async () => fireEvent.click(screen.getByRole("checkbox")));
    expect(api.deleteEmaAlert).toHaveBeenCalledWith(3);
  });

  it("connects Telegram with a one-time code and the bot link", async () => {
    api.getEmaAlerts.mockResolvedValue({ telegram_linked: false, bot_username: "convexa_alertas_bot", rules: [] });
    api.newTelegramLinkCode.mockResolvedValue({ code: "ABC234", expires_in_seconds: 600, bot_username: "convexa_alertas_bot", link: "https://t.me/convexa_alertas_bot?start=ABC234" });
    show();
    const connect = await screen.findByRole("button", { name: /Telegram/ });
    await waitFor(() => expect(connect).not.toBeDisabled());
    await act(async () => fireEvent.click(connect));
    expect(await screen.findByText(/ABC234/)).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "https://t.me/convexa_alertas_bot?start=ABC234");
  });

  it("says which timeframes work instead of a toggle on other charts, and an invalid pair cannot be saved", async () => {
    api.getEmaAlerts.mockResolvedValue({ telegram_linked: true, bot_username: "b", rules: [] });
    const { unmount } = show("SPX", "30m");
    expect(await screen.findByText(/1m, 5m, 15m/)).toBeInTheDocument();
    unmount();
    show("SPX", "1m");
    const fast = await screen.findByLabelText(/rápida|Fast/);
    fireEvent.change(fast, { target: { value: "30" } });
    await waitFor(() => expect(screen.getByRole("checkbox")).toBeDisabled());
  });
});
