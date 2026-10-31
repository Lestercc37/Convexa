"use client";

import { AlertsPanel } from "@/components/alerts-panel";
import { PanelWindow } from "@/components/v2-panel-window";

// The alerts list in its own desktop-app window (the left column of the web dashboard), for another screen: /vista/alertas?simbolo=SPX
export default function AlertsWindowPage() {
  return <PanelWindow title="Alertas">{(symbol) => <AlertsPanel key={symbol} symbol={symbol} orientation="vertical" />}</PanelWindow>;
}
