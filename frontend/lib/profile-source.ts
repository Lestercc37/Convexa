import type { GammaHeatmapMetric, GammaView } from "@/lib/types";

// Where the GEX profile column takes its bars from (frontend/components/price-chart.tsx).
// - "heatmap": the heatmap's own latest columns (its figure, the sides, and on the 0DTE tabs the fast layer re-priced every 2 s);
// - "fast-tail": the heatmap is off but the 0DTE tab has a fast layer: only its last few columns are read for the profile;
// - "endpoint": GET /gamma/{symbol}/profile, the REST cycle's profile (about once a minute).
// The fast layer drives the profile ONLY on the 0DTE tabs (Lester 2026-11-05: "activalo solo para 0DTE"); 0-2 DTE, 1DTE, 2DTE and 90 días keep the cycle's.
export type ProfileSource = "heatmap" | "fast-tail" | "endpoint";

const FAST_PROFILE_VIEWS: readonly GammaView[] = ["0dte", "0dte_volume"];

export function followsFastLayer(view: GammaView): boolean {
  return FAST_PROFILE_VIEWS.includes(view);
}

export function profileSource(options: {
  view: GammaView;
  showHeatmap: boolean;
  sidesOn: boolean;
  metric: GammaHeatmapMetric;
  heatmapIsFast: boolean;
  fastTailHasBars: boolean;
}): ProfileSource {
  const { view, showHeatmap, sidesOn, metric, heatmapIsFast, fastTailHasBars } = options;
  if (showHeatmap && sidesOn) return "heatmap";
  const endpointServesIt = metric === "gamma" || metric === "absolute_gamma";
  if (!endpointServesIt) return "heatmap";
  if (followsFastLayer(view) && showHeatmap && heatmapIsFast) return "heatmap";
  if (followsFastLayer(view) && !showHeatmap && fastTailHasBars) return "fast-tail";
  return "endpoint";
}
