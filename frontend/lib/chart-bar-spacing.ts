import type { IChartApi, Logical } from "lightweight-charts";

// Widest a candle may get, in px, when the chart shows the fitted "whole
// session" view. fitContent() divides the plot width by the bar count, so a
// coarse timeframe (79 bars at 5m, 26 at 15m) gets 5x-15x the pixels per
// bar a 1m chart (~390 bars) does.
export const MAX_BAR_SPACING_PX = 6;

// Live pixels-per-bar: the on-screen distance between two adjacent logical
// indices. Only valid once the library has painted a frame after the last
// range change -- fitContent()/setVisibleLogicalRange() are queued and applied
// on the next frame, so reading this right after calling them returns the
// OLD value (measured 2026-10-02: 6 right after fitContent(), 16.775 one
// frame later, same chart).
export function barSpacingPx(chart: IChartApi): number | null {
  const scale = chart.timeScale();
  const first = scale.logicalToCoordinate(0 as Logical);
  const second = scale.logicalToCoordinate(1 as Logical);
  if (first === null || second === null) return null;
  return second - first;
}

// fitContent(), except that when fitting every bar would make each one wider
// than MAX_BAR_SPACING_PX the visible range is widened instead, so bars are
// exactly MAX_BAR_SPACING_PX wide and the leftover width is blank space to
// the right of the last bar. Decides from the bar count and plot width, not
// from the chart's current spacing, because that is not updated until the
// next frame (see barSpacingPx). lastLogicalIndex includes the leading
// whitespace anchor, same as the bars fitContent() itself counts. plotWidth
// defaults to the chart's current plot width; a size-change callback should
// pass the width it was given, which is the post-layout one (the plot is
// narrower than the container once the price axis is laid out).
export function fitContentCappingBarSpacing(
  chart: IChartApi,
  lastLogicalIndex: number,
  plotWidth: number = chart.timeScale().width(),
): void {
  const scale = chart.timeScale();
  const width = plotWidth;
  const barCount = lastLogicalIndex + 1;
  if (width > 0 && barCount > 0 && width / barCount > MAX_BAR_SPACING_PX) {
    scale.setVisibleLogicalRange({ from: 0, to: width / MAX_BAR_SPACING_PX - 1 });
    return;
  }
  scale.fitContent();
}
