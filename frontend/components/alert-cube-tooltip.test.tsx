import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { AlertCube } from "@/lib/alert-cubes";
import { es } from "@/lib/i18n/es";
import type { WhaleAlert } from "@/lib/types";
import { AlertCubeTooltip, tradeTimeLabel } from "./alert-cube-tooltip";

const ISSUED = "2026-10-16T14:42:11Z"; // 10:42:11 ET (EDT)

function cubeFor(overrides: Partial<WhaleAlert> = {}): AlertCube {
  const alert: WhaleAlert = {
    symbol: "SPX",
    contract: "SPXW261016C07720000",
    type: "UNUSUAL",
    amount: 65000,
    timestamp: ISSUED,
    estimated_buy_volume: 65000,
    estimated_sell_volume: 0,
    quote_unavailable: false,
    moneyness: "ATM",
    near_gamma_level: null,
    repeat_count: 1,
    delta: 0.5,
    contracts: 100,
    ...overrides,
  };
  return { id: "x", time: Math.floor(Date.parse(ISSUED) / 1000), strike: 7720, right: "C", deq: 50, netDeq: 50, size: 9, side: "buy", zone: "ATM", alert };
}

function renderTooltip(cube: AlertCube) {
  return render(<AlertCubeTooltip cube={cube} price={7700} t={es.priceChart} style={{}} />);
}

describe("tradeTimeLabel", () => {
  it("is the minute for one minute and the range for a sustained window; null without a trade time", () => {
    expect(tradeTimeLabel("2026-10-16T14:40:00Z", 1)).toBe("10:40");
    expect(tradeTimeLabel("2026-10-16T14:26:00Z", 15)).toBe("10:26–10:41");
    expect(tradeTimeLabel("2026-10-16T14:40:00Z", null)).toBe("10:40");
    expect(tradeTimeLabel(null, 1)).toBeNull();
    expect(tradeTimeLabel(undefined, undefined)).toBeNull();
    expect(tradeTimeLabel("not a date", 1)).toBeNull();
  });
});

describe("AlertCubeTooltip time", () => {
  it("leads with when it was traded and says when the alert was issued", () => {
    const { container } = renderTooltip(cubeFor({ trade_start: "2026-10-16T14:40:00Z", trade_minutes: 1 }));
    expect(container.textContent).toContain("10:40");
    expect(container.textContent).toContain("Alerta emitida 10:42:11");
  });

  it("keeps the issue time with seconds for older alerts that have no trade time", () => {
    const { container } = renderTooltip(cubeFor());
    expect(container.textContent).toContain("10:42:11");
    expect(screen.queryByText(/Alerta emitida/)).toBeNull();
  });
});

describe("AlertCubeTooltip sizes", () => {
  it("shows the gross size and, under it, the net size with the buy / sell premium", () => {
    const cube = { ...cubeFor({ estimated_buy_volume: 40000, estimated_sell_volume: 25000 }), deq: 50, netDeq: 12 };
    const { container } = renderTooltip(cube);
    expect(container.textContent).toContain("Tamaño: 50 contratos-delta");
    expect(container.textContent).toContain("Neto: 12 contratos-delta · compra $40k / venta $25k");
  });

  it("has no net line when the alert had no quote to sign against", () => {
    const { container } = renderTooltip(cubeFor({ quote_unavailable: true }));
    expect(container.textContent).toContain("Tamaño: 50 contratos-delta");
    expect(container.textContent).not.toContain("Neto:");
  });
});
