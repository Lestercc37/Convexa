export const POLLING_INTERVAL_MS = 30_000;
/** The chart's gamma layers (heatmap, GEX histogram) are polled faster than the other panels: the backend writes a new profile about every minute. */
export const LIVE_LEVELS_POLLING_INTERVAL_MS = 15_000;
