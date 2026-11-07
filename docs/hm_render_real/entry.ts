import { createChart, CandlestickSeries } from "lightweight-charts";
import { GammaHeatmapPrimitive, heatmapFromResponse } from "@/components/gamma-heatmap-primitive";
import dump from "./dump.json";

const params = new URLSearchParams(location.search);
const width = Number(params.get("w") ?? 1100);
const height = Number(params.get("h") ?? 520);
const el = document.getElementById("c") as HTMLElement;
const chart = createChart(el, {
  width,
  height,
  layout: { background: { color: "#0b0f17" }, textColor: "#9aa4b2" },
  grid: { vertLines: { color: "#161c27" }, horzLines: { color: "#161c27" } },
  timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#2a3342" },
  rightPriceScale: { borderColor: "#2a3342" },
});
const series = chart.addSeries(CandlestickSeries, {
  upColor: "#26a69a", downColor: "#ef5350", borderVisible: false, wickUpColor: "#26a69a", wickDownColor: "#ef5350",
});
series.setData((dump as any).candles);
const primitive = new GammaHeatmapPrimitive();
series.attachPrimitive(primitive);
primitive.setData(heatmapFromResponse((dump as any).heatmap));
chart.timeScale().fitContent();
setTimeout(() => {
  const strikes: number[] = (dump as any).heatmap.strikes;
  const mid = strikes[Math.floor(strikes.length / 2)];
  const y1 = series.priceToCoordinate(mid), y2 = series.priceToCoordinate(mid + 5);
  const cols = (dump as any).heatmap.columns as { as_of: string }[];
  const t = (i: number) => Math.floor(Date.parse(cols[i].as_of) / 60000) * 60;
  const x1 = chart.timeScale().timeToCoordinate(t(40) as any), x2 = chart.timeScale().timeToCoordinate(t(41) as any);
  (window as any).__metrics = { width, height, cellHeightPx: y1 !== null && y2 !== null ? Math.abs((y1 as number) - (y2 as number)) : null, barSpacingPx: chart.timeScale().options().barSpacing, columns: cols.length, strikes: strikes.length };
  document.getElementById("m")!.textContent = JSON.stringify((window as any).__metrics);
}, 300);
