"use client";

import { conditionShares, trialLabel, type AlertCube, type TrialLabel } from "@/lib/alert-cubes";
import type { Translations } from "@/lib/i18n/translations";

// The text of one alert cube, shown only while it is hovered: time and type, the side (COMPRA / VENTA / SIN LADO CLARO, no percentages), call or
// put with its strike and delta zone, size in delta contracts with premium and repeats, the trade-condition mix, the second label of the trial,
// and how far the strike is from the price.

function formatPremium(amount: number): string {
  return amount >= 1_000_000 ? `$${(amount / 1_000_000).toFixed(2)}M` : `$${Math.round(amount / 1000)}k`;
}

function sideColor(side: "buy" | "sell" | "mixed" | "none"): string {
  return side === "buy" ? "var(--calm)" : side === "sell" ? "#ff6b70" : side === "mixed" ? "var(--warning)" : "var(--tv-text-dim)";
}

const EASTERN_TIME_FORMAT = new Intl.DateTimeFormat("en-GB", {
  timeZone: "America/New_York",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function AlertCubeTooltip({
  cube,
  price,
  t,
  style,
}: {
  cube: AlertCube;
  price: number | null;
  t: Translations["priceChart"];
  style: React.CSSProperties;
}) {
  const { alert } = cube;
  const shares = conditionShares(alert.condition_premium);
  const mix = shares?.filter((share) => share.percent >= 10).slice(0, 2) ?? [];
  const multileg = shares?.find((share) => share.category === "multileg")?.percent ?? 0;
  const trial: TrialLabel | null = trialLabel(alert.simple_flow, alert.amount);
  return (
    <div className="alert-cube-tooltip" role="tooltip" style={style}>
      <div>
        <strong>{EASTERN_TIME_FORMAT.format(new Date(cube.time * 1000))}</strong> · {t.cubeTypes[alert.type]}
      </div>
      <div>
        <strong style={{ color: sideColor(cube.side) }}>{t.cubeSides[cube.side]}</strong> · {t.cubeRight[cube.right]} {cube.strike} (
        {t.cubeZones[cube.zone]}
        {alert.delta != null ? `, delta ${alert.delta.toFixed(2)}` : ""})
      </div>
      <div>{t.cubeSizeLine(cube.deq, formatPremium(alert.amount), alert.repeat_count)}</div>
      {mix.length > 0 && (
        <div>
          {t.cubeConditionTitle}: {mix.map((share) => `${t.cubeCategories[share.category]} ${share.percent} %`).join(" · ")}
        </div>
      )}
      {multileg >= 50 && <div style={{ color: "var(--warning)" }}>{t.cubeMultilegWarning}</div>}
      {trial !== null && <div>{t.cubeTrialLine(t.cubeSides[trial])}</div>}
      {price !== null && <div className="alert-cube-tooltip-dim">{t.cubeDistanceLine(cube.strike - price)}</div>}
    </div>
  );
}
