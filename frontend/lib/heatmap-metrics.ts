import type { GammaHeatmapMetric } from "@/lib/types";

// The metric tabs of the heatmap, in the order of the approved mockup (Gamma | Delta) plus absolute gamma, vanna and charm. Shared by the main chart and the Replay window.
export const HEATMAP_METRIC_TABS = [
  { metric: "gamma", labelKey: "heatmapMetricGamma" },
  { metric: "absolute_gamma", labelKey: "heatmapMetricAbsolute" },
  { metric: "total_gamma", labelKey: "heatmapMetricTotal" },
  { metric: "delta", labelKey: "heatmapMetricDelta" },
  { metric: "vanna", labelKey: "heatmapMetricVanna" },
  { metric: "charm", labelKey: "heatmapMetricCharm" },
] as const satisfies readonly { metric: GammaHeatmapMetric; labelKey: string }[];
