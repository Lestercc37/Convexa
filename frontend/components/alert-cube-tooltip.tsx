"use client";

import { conditionShares, trialLabel, type AlertCube, type TrialLabel } from "@/lib/alert-cubes";
import type { Translations } from "@/lib/i18n/translations";

// The text of one alert cube, shown only while it is hovered: when it was TRADED (the minute, or the 15 minutes of a sustained flow; older alerts only have the issue time) and its type, when the alert was issued, the side (COMPRA / VENTA / SIN LADO CLARO, no percentages), call or
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

const EASTERN_MINUTE_FORMAT = new Intl.DateTimeFormat("en-GB", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false });

// When the trades happened: "10:40" for a one-minute bucket, "10:26–10:41" for the 15 minutes of a sustained flow; null for alerts that carry no trade time (written before migration 0049).
export function tradeTimeLabel(tradeStart: string | null | undefined, tradeMinutes: number | null | undefined): string | null {
  const start = tradeStart ? Date.parse(tradeStart) : Number.NaN;
  if (!Number.isFinite(start)) return null;
  const first = EASTERN_MINUTE_FORMAT.format(new Date(start));
  const minutes = tradeMinutes && tradeMinutes > 1 ? tradeMinutes : 1;
  return minutes === 1 ? first : `${first}–${EASTERN_MINUTE_FORMAT.format(new Date(start + minutes * 60_000))}`;
}

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
  const issuedAt = EASTERN_TIME_FORMAT.format(new Date(cube.time * 1000));
  const tradedAt = tradeTimeLabel(alert.trade_start, alert.trade_minutes);
  const shares = conditionShares(alert.condition_premium);
  const mix = shares?.filter((share) => share.percent >= 10).slice(0, 2) ?? [];
  const multileg = shares?.find((share) => share.category === "multileg")?.percent ?? 0;
  const trial: TrialLabel | null = trialLabel(alert.simple_flow, alert.amount);
  return (
    <div className="alert-cube-tooltip" role="tooltip" style={style}>
      <div>
        <strong>{tradedAt ?? issuedAt}</strong> · {t.cubeTypes[alert.type]}
      </div>
      {tradedAt !== null && <div className="alert-cube-tooltip-dim">{t.cubeIssuedLine(issuedAt)}</div>}
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
