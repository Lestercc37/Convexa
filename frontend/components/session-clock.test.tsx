import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageProvider } from "@/lib/i18n/language-context";
import { SessionClock } from "./session-clock";

describe("SessionClock", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows New York time with seconds and moves every second", () => {
    vi.setSystemTime(new Date("2026-10-20T14:42:31Z")); // 10:42:31 ET (EDT)
    render(
      <LanguageProvider>
        <SessionClock />
      </LanguageProvider>,
    );
    expect(screen.getByRole("timer").textContent).toBe("10:42:31 ET");
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByRole("timer").textContent).toBe("10:42:32 ET");
  });

  it("follows the daylight-saving change (EST in winter)", () => {
    vi.setSystemTime(new Date("2026-12-15T14:30:05Z")); // 09:30:05 ET (EST)
    render(
      <LanguageProvider>
        <SessionClock />
      </LanguageProvider>,
    );
    expect(screen.getByRole("timer").textContent).toBe("09:30:05 ET");
  });
});
