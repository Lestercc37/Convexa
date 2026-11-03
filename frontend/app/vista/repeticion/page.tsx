"use client";

import { ReplayPanel } from "@/components/replay-panel";
import { PanelWindow } from "@/components/v2-panel-window";

// Replay of a stored session in its own desktop-app window: /vista/repeticion?simbolo=SPX&dia=2026-10-30.
export default function ReplayWindowPage() {
  return <PanelWindow title="Repetición">{(symbol) => <ReplayPanel key={symbol} symbol={symbol} />}</PanelWindow>;
}
