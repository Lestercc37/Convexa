"use client";

import { FlowChainPanel } from "@/components/flow-chain-panel";
import { PanelWindow } from "@/components/v2-panel-window";

// The flow chain in its own desktop-app window: /vista/cadena?simbolo=SPX (docs/diseno-cadena-flujo-2026-10-25.txt).
export default function FlowChainWindowPage() {
  return <PanelWindow title="Cadena de flujo">{(symbol) => <FlowChainPanel key={symbol} symbol={symbol} />}</PanelWindow>;
}
