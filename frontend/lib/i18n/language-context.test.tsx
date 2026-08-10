import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { LanguageProvider, useLanguage } from "./language-context";

const STORAGE_KEY = "convexa-language";

afterEach(() => {
  window.localStorage.clear();
});

describe("useLanguage / LanguageProvider", () => {
  it("defaults to Spanish and switches to English on setLanguage", async () => {
    const { result } = renderHook(() => useLanguage(), { wrapper: LanguageProvider });

    expect(result.current.language).toBe("es");
    expect(result.current.t.dashboard.liveButton).toBe("En vivo");

    act(() => result.current.setLanguage("en"));

    await waitFor(() => expect(result.current.language).toBe("en"));
    expect(result.current.t.dashboard.liveButton).toBe("Live");
  });

  it("switches back from English to Spanish", async () => {
    const { result } = renderHook(() => useLanguage(), { wrapper: LanguageProvider });

    act(() => result.current.setLanguage("en"));
    await waitFor(() => expect(result.current.language).toBe("en"));

    act(() => result.current.setLanguage("es"));
    await waitFor(() => expect(result.current.language).toBe("es"));
    expect(result.current.t.regimeBadge.currentRegimeEyebrow).toBe("Régimen actual");
  });

  it("persists the chosen language to localStorage", async () => {
    const { result } = renderHook(() => useLanguage(), { wrapper: LanguageProvider });

    act(() => result.current.setLanguage("en"));

    await waitFor(() => expect(window.localStorage.getItem(STORAGE_KEY)).toBe("en"));
  });

  it("restores the stored language on the next mount (simulated reload)", async () => {
    window.localStorage.setItem(STORAGE_KEY, "en");

    const { result } = renderHook(() => useLanguage(), { wrapper: LanguageProvider });

    // Starts at the SSR-safe default ("es") for one frame, then reads
    // localStorage post-mount — see the comment in language-context.tsx on
    // why this can't be read synchronously without a hydration mismatch.
    await waitFor(() => expect(result.current.language).toBe("en"));
    expect(result.current.t.dashboard.liveButton).toBe("Live");
  });

  it("ignores a corrupted/unknown stored value and falls back to the default", async () => {
    window.localStorage.setItem(STORAGE_KEY, "fr");

    const { result } = renderHook(() => useLanguage(), { wrapper: LanguageProvider });

    await waitFor(() => expect(result.current.language).toBe("es"));
  });
});
