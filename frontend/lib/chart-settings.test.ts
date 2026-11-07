import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ getChartSettings: vi.fn(), putChartSettings: vi.fn() }));
vi.mock("@/lib/api", () => api);

import { getChartSetting, loadChartSettings, resetChartSettingsForTests, setChartSetting, useChartSetting } from "./chart-settings";

describe("trader chart template", () => {
  beforeEach(() => {
    window.localStorage.clear();
    resetChartSettingsForTests();
    api.getChartSettings.mockReset();
    api.putChartSettings.mockReset();
    api.putChartSettings.mockResolvedValue({ schema_version: 1, saved: true });
  });
  afterEach(() => vi.useRealTimers());

  it("takes the server's template, keeps what changed before it arrived, and saves a change after a moment", async () => {
    let answer: (value: unknown) => void = () => undefined;
    api.getChartSettings.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    const { result } = renderHook(() => useChartSetting("heatmap", false));
    expect(result.current[0]).toBe(false);
    act(() => result.current[1](true)); // clicked before the server answered
    await act(async () => answer({ schema_version: 1, settings: { heatmap: false, heatmapMetric: "absolute_gamma" } }));
    await waitFor(() => expect(getChartSetting("heatmapMetric")).toBe("absolute_gamma"));
    expect(result.current[0]).toBe(true);
    await waitFor(() => expect(api.putChartSettings).toHaveBeenCalledWith({ heatmap: true, heatmapMetric: "absolute_gamma" }), { timeout: 2000 });
  });

  it("a link's preset wins on that chart and is never saved; a value of the wrong kind falls back", async () => {
    api.getChartSettings.mockResolvedValue({ schema_version: 1, settings: { profile: "yes" } });
    await loadChartSettings();
    const { result } = renderHook(() => useChartSetting("flow", false, true));
    expect(result.current[0]).toBe(true);
    act(() => result.current[1](false));
    expect(result.current[0]).toBe(false);
    expect(getChartSetting("flow")).toBeUndefined();
    const profile = renderHook(() => useChartSetting("profile", true));
    expect(profile.result.current[0]).toBe(true);
  });

  it("a layer window starts from its preset and saves its own choices under its own key, never the main chart's", async () => {
    api.getChartSettings.mockResolvedValue({ schema_version: 1, settings: { heatmap: false } });
    await loadChartSettings();
    const { result } = renderHook(() => useChartSetting("heatmap", false, true, "heatmap-abs"));
    expect(result.current[0]).toBe(true);
    act(() => result.current[1](false));
    expect(result.current[0]).toBe(false);
    expect(getChartSetting("heatmap@heatmap-abs")).toBe(false);
    expect(getChartSetting("heatmap")).toBe(false);
    await waitFor(() => expect(api.putChartSettings).toHaveBeenCalledWith({ heatmap: false, "heatmap@heatmap-abs": false }), { timeout: 2000 });
  });

  it("a save writes only this window's changes over the server's current copy (another window's change survives)", async () => {
    api.getChartSettings.mockResolvedValueOnce({ schema_version: 1, settings: { heatmap: false, profile: true } });
    await loadChartSettings();
    // meanwhile another window switched the profile off and saved it
    api.getChartSettings.mockResolvedValue({ schema_version: 1, settings: { heatmap: false, profile: false } });
    act(() => setChartSetting("heatmap", true));
    await waitFor(() => expect(api.putChartSettings).toHaveBeenCalledWith({ heatmap: true, profile: false }), { timeout: 2000 });
    await waitFor(() => expect(getChartSetting("profile")).toBe(false));
  });

  it("another window's change arrives through the browser copy", async () => {
    api.getChartSettings.mockResolvedValue({ schema_version: 1, settings: { heatmap: false } });
    await loadChartSettings();
    const { result } = renderHook(() => useChartSetting("heatmap", false));
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: "convexa.chartSettings.v1", newValue: JSON.stringify({ heatmap: true }) }));
    });
    expect(result.current[0]).toBe(true);
  });
});
