import type { ISeriesApi, ISeriesPrimitive, ISeriesPrimitiveAxisView, SeriesAttachedParameter, Time } from "lightweight-charts";

// The countdown of the forming candle as a second label on the price axis, right under the last-price tag (the TradingView way). `read` is called while the chart draws, so the label
// follows every tick of the price with no lag; refresh() only asks for a redraw (once a second, to move the digits).

export type CandleCountdownReading = { price: number | null; secondsText: string | null; up: boolean };

export const COUNTDOWN_UP_COLOR = "#26A69A";
export const COUNTDOWN_DOWN_COLOR = "#EF5350";
// Distance from the center of the last-price tag to the center of the countdown tag, in CSS pixels (a price tag is about this tall).
export const COUNTDOWN_OFFSET_PX = 19;

export class CandleCountdownPrimitive implements ISeriesPrimitive<Time> {
  private series: ISeriesApi<"Candlestick"> | null = null;
  private requestUpdate: (() => void) | null = null;
  private readonly axisView: ISeriesPrimitiveAxisView;

  constructor(private readonly read: () => CandleCountdownReading) {
    const y = (): number | null => {
      const { price } = this.read();
      if (price === null || !this.series) return null;
      const coordinate = this.series.priceToCoordinate(price);
      return coordinate === null || !Number.isFinite(coordinate) ? null : coordinate + COUNTDOWN_OFFSET_PX;
    };
    this.axisView = {
      // A fixed coordinate keeps the label glued under the price tag; the large negative `coordinate` stops the automatic placement from leaving a gap for it.
      coordinate: () => -10_000,
      fixedCoordinate: () => y() ?? undefined,
      text: () => this.read().secondsText ?? "",
      textColor: () => "#FFFFFF",
      backColor: () => (this.read().up ? COUNTDOWN_UP_COLOR : COUNTDOWN_DOWN_COLOR),
      visible: () => this.read().secondsText !== null && y() !== null,
      tickVisible: () => false,
    };
  }

  attached(parameter: SeriesAttachedParameter<Time>): void {
    this.series = parameter.series as ISeriesApi<"Candlestick">;
    this.requestUpdate = parameter.requestUpdate;
  }

  detached(): void {
    this.series = null;
    this.requestUpdate = null;
  }

  priceAxisViews(): readonly ISeriesPrimitiveAxisView[] {
    return [this.axisView];
  }

  refresh(): void {
    this.requestUpdate?.();
  }
}
