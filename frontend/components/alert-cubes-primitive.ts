import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  PrimitiveHoveredItem,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";
import type { AlertCube, CubeSide } from "@/lib/alert-cubes";
import { timeToX } from "@/lib/chart-time";

// Alert cubes drawn ON TOP of the candles at the chart's own coordinates (timeToX in lib/chart-time.ts / priceToCoordinate), so they follow zoom and
// scrolling. A strike outside the visible price range is pinned to the pane's top or bottom edge and drawn fainter; cubes that land on the same
// spot are laid side by side. hitTest() reports the hovered cube to the chart (hoveredObjectId) for the tooltip.

export const CUBE_COLORS: Record<CubeSide, string> = {
  buy: "#00DC5A",
  sell: "#FF4D55",
  mixed: "#F3C969",
};
const OUTSIDE_ALPHA = 0.45;
const EDGE_MARGIN = 8;
const STACK_GAP = 2;

type DrawTarget = Parameters<IPrimitivePaneRenderer["draw"]>[0];

type Placed = { id: string; x: number; y: number; size: number };

class AlertCubesRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly source: AlertCubesPrimitive) {}

  draw(target: DrawTarget): void {
    target.useBitmapCoordinateSpace((scope) => {
      const { series, chart, cubes } = this.source;
      const placed: Placed[] = [];
      this.source.placed = placed;
      if (!series || !chart || !cubes.length) return;
      const timeScale = chart.timeScale();
      const paneHeight = scope.mediaSize.height;
      const stacked = new Map<string, number>();

      for (const cube of cubes) {
        const rawX = timeToX(timeScale, series, cube.time);
        const rawY = series.priceToCoordinate(cube.strike);
        if (rawX === null || !Number.isFinite(rawX) || rawY === null || !Number.isFinite(rawY)) continue;
        const inside = rawY >= EDGE_MARGIN && rawY <= paneHeight - EDGE_MARGIN;
        const y = inside ? rawY : rawY < EDGE_MARGIN ? EDGE_MARGIN : paneHeight - EDGE_MARGIN;
        const key = `${Math.round(rawX / 6)}:${Math.round(y / 6)}`;
        const position = stacked.get(key) ?? 0;
        stacked.set(key, position + 1);
        const x = rawX + position * (cube.size + STACK_GAP);
        placed.push({ id: cube.id, x, y, size: cube.size });

        const context = scope.context;
        const left = Math.round((x - cube.size / 2) * scope.horizontalPixelRatio);
        const top = Math.round((y - cube.size / 2) * scope.verticalPixelRatio);
        const width = Math.round(cube.size * scope.horizontalPixelRatio);
        const height = Math.round(cube.size * scope.verticalPixelRatio);
        context.save();
        context.globalAlpha = inside ? 1 : OUTSIDE_ALPHA;
        const hovered = this.source.hoveredId === cube.id;
        if (cube.kind === "clear") {
          context.fillStyle = CUBE_COLORS[cube.side];
          context.fillRect(left, top, width, height);
          context.strokeStyle = hovered ? "#FFFFFF" : "#131722";
          context.lineWidth = hovered ? 2 : 1;
          context.strokeRect(left + 0.5, top + 0.5, width - 1, height - 1);
        } else {
          // Neutral circles, never green / red: a big trade is a filled light-grey disc, a multi-leg package a light-grey ring.
          const radius = width / 2;
          context.beginPath();
          context.arc(left + radius, top + radius, Math.max(1, radius - 0.5), 0, Math.PI * 2);
          if (cube.kind === "big") {
            context.fillStyle = "#cfd5dc";
            context.fill();
          }
          context.strokeStyle = hovered ? "#FFFFFF" : "#cfd5dc";
          context.lineWidth = (hovered ? 2 : 1.5) * scope.horizontalPixelRatio;
          context.stroke();
        }
        context.restore();
      }
    });
  }
}

export class AlertCubesPrimitive implements ISeriesPrimitive<Time> {
  series: ISeriesApi<"Candlestick"> | null = null;
  chart: SeriesAttachedParameter<Time>["chart"] | null = null;
  cubes: AlertCube[] = [];
  /** Where the last draw put each cube, in CSS pixels of the pane (what hitTest answers from). */
  placed: Placed[] = [];
  hoveredId: string | null = null;
  private requestUpdate: (() => void) | null = null;
  private readonly view: IPrimitivePaneView;

  constructor() {
    const renderer = new AlertCubesRenderer(this);
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
    this.placed = [];
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  setCubes(cubes: AlertCube[]): void {
    this.cubes = cubes;
    this.requestUpdate?.();
  }

  setHovered(id: string | null): void {
    if (this.hoveredId === id) return;
    this.hoveredId = id;
    this.requestUpdate?.();
  }

  hitTest(x: number, y: number): PrimitiveHoveredItem | null {
    // The latest cube wins when several overlap.
    for (let index = this.placed.length - 1; index >= 0; index -= 1) {
      const cube = this.placed[index];
      const half = cube.size / 2 + 1;
      if (Math.abs(x - cube.x) <= half && Math.abs(y - cube.y) <= half) {
        return { cursorStyle: "pointer", externalId: cube.id, zOrder: "top" };
      }
    }
    return null;
  }
}
