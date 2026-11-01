"use client";

import { ContractChartPanel } from "@/components/contract-chart-panel";
import { PanelWindow } from "@/components/v2-panel-window";

// Call | Put of one 0DTE strike in its own desktop-app window: /vista/contrato?simbolo=SPX.
export default function ContractWindowPage() {
  return <PanelWindow title="Contratos Call | Put">{(symbol) => <ContractChartPanel key={symbol} symbol={symbol} />}</PanelWindow>;
}
