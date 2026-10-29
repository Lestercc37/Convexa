import { describe, expect, it } from "vitest";
import { chartRouteQuery, layerPreset, parseChartRoute } from "./view-route";

describe("chart view route", () => {
  it("reads every field from the address", () => {
    const state = parseChartRoute(new URLSearchParams("simbolo=ndx&tf=5m&rango=semana&dte=1dte&capa=heatmap-delta"));
    expect(state).toEqual({ symbol: "NDX", timeframe: "5m", range: "week", gammaTab: "1dte", layer: "heatmap-delta" });
  });

  it("ignores unknown or malformed values", () => {
    const state = parseChartRoute(new URLSearchParams("simbolo=<script>&tf=2m&rango=mes&dte=structural&capa=everything"));
    expect(state).toEqual({ symbol: null, timeframe: null, range: null, gammaTab: null, layer: null });
  });

  it("round-trips through the query string", () => {
    const state = { symbol: "SPX", timeframe: "1m" as const, range: "day" as const, gammaTab: "tactical" as const, layer: "profile" as const };
    expect(parseChartRoute(new URLSearchParams(chartRouteQuery(state)))).toEqual(state);
  });

  it("a plain chart turns every layer off; a layer turns only itself on", () => {
    expect(layerPreset(null)).toEqual({ profile: false, smile: false, flow: false, cubes: false, heatmap: null });
    expect(layerPreset("heatmap-abs")).toEqual({ profile: false, smile: false, flow: false, cubes: false, heatmap: "absolute_gamma" });
    expect(layerPreset("heatmap-puts").heatmap).toBe("puts");
    expect(layerPreset("cubes").cubes).toBe(true);
  });
});
