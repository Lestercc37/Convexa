import type { IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesApi, ISeriesPrimitive, SeriesAttachedParameter, Time } from "lightweight-charts";
import { timeToX } from "@/lib/chart-time";
import type { OrderFlowEvent } from "@/lib/types";

// Order-flow events detected in the owner's NinjaTrader on ES / NQ / RTY ('Convexa Detector', 2026-11-06), drawn on SPX / SPY, NDX / QQQ and
// IWM at the translated price (backend use_cases/futures_events.py): a diamond for absorption, a triangle for a stack of imbalances (up for
// buyers, down for sellers) and a small dot for a big trade. Green = buyers win, red = sellers win; big trades blue / orange. Drawn on top of
// the candles at the chart's own coordinates, so they follow zoom and scrolling; an event outside the visible prices is not drawn.

export const ORDER_FLOW_COLORS = { buy: "#22C55E", sell: "#EF4444", bigBuy: "#38BDF8", bigSell: "#F97316" } as const;
const SIZE_CSS_PX = 6;

type DrawTarget = Parameters<IPrimitivePaneRenderer["draw"]>[0];

/** The colour of one event: absorption and stacks by side, big trades in their own pair so they read apart from the others. */
export function orderFlowColor(event: Pick<OrderFlowEvent, "type" | "side">): string {
  if (event.type === "big_trade") return event.side === "buy" ? ORDER_FLOW_COLORS.bigBuy : ORDER_FLOW_COLORS.bigSell;
  return event.side === "buy" ? ORDER_FLOW_COLORS.buy : ORDER_FLOW_COLORS.sell;
}

class OrderFlowEventsRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly source: OrderFlowEventsPrimitive) {}

  draw(target: DrawTarget): void {
    target.useBitmapCoordinateSpace((scope) => {
      const { series, chart, events } = this.source;
      if (!series || !chart || !events.length) return;
      const timeScale = chart.timeScale();
      const ctx = scope.context;
      const s = SIZE_CSS_PX * scope.horizontalPixelRatio;
      for (const event of events) {
        const x = timeToX(timeScale, series, Math.floor(Date.parse(event.at) / 1000));
        const y = series.priceToCoordinate(event.price);
        if (x === null || y === null || !Number.isFinite(x) || !Number.isFinite(y)) continue;
        const px = x * scope.horizontalPixelRatio;
        const py = y * scope.verticalPixelRatio;
        ctx.fillStyle = orderFlowColor(event);
        ctx.strokeStyle = "rgba(10, 12, 18, 0.9)";
        ctx.lineWidth = scope.horizontalPixelRatio;
        ctx.beginPath();
        if (event.type === "absorption") {
          ctx.moveTo(px, py - s);
          ctx.lineTo(px + s, py);
          ctx.lineTo(px, py + s);
          ctx.lineTo(px - s, py);
        } else if (event.type === "stacked_imbalance") {
          const dir = event.side === "buy" ? -1 : 1;
          ctx.moveTo(px, py + dir * s);
          ctx.lineTo(px + s, py - dir * s);
          ctx.lineTo(px - s, py - dir * s);
        } else {
          ctx.arc(px, py, s * 0.6, 0, Math.PI * 2);
        }
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
      }
    });
  }
}

export class OrderFlowEventsPrimitive implements ISeriesPrimitive<Time> {
  series: ISeriesApi<"Candlestick"> | null = null;
  chart: SeriesAttachedParameter<Time>["chart"] | null = null;
  events: OrderFlowEvent[] = [];
  private requestUpdate: (() => void) | null = null;
  private readonly view: IPrimitivePaneView;

  constructor() {
    const renderer = new OrderFlowEventsRenderer(this);
    this.view = { zOrder: () => "top", renderer: () => renderer };
  }

  attached(parameter: SeriesAttachedParameter<Time>): void {
    this.series = parameter.series as ISeriesApi<"Candlestick">;
    this.chart = parameter.chart;
    this.requestUpdate = parameter.requestUpdate;
  }

  detached(): void {
    this.series = null;
    this.chart = null;
    this.requestUpdate = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  setEvents(events: OrderFlowEvent[]): void {
    this.events = events;
    this.requestUpdate?.();
  }
}
