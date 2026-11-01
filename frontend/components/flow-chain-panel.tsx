"use client";

import { useEffect, useMemo, useState } from "react";
import { getGamma, getMarket, getStrikeFlow } from "@/lib/api";
import { DEFAULT_GAMMA_VIEW } from "@/lib/gamma-views";
import { useLanguage } from "@/lib/i18n/language-context";
import { POLLING_INTERVAL_MS } from "@/lib/polling";
import type { StrikeFlowResponse, StrikeFlowRow, StrikeFlowWindows } from "@/lib/types";

/**
 * The flow chain (docs/diseno-cadena-flujo-2026-10-25.txt, Lester's gex-flow.html): per strike, the signed option flow of the last
 * 5 / 15 / 30 / 60 finished minutes and of the day (GET /flow/{symbol}/strikes, table minute_strike_flow), ~14 strikes above and below
 * the price, Convexa's own levels in the last column, a magnet on the strike that took the most net flow in each window, and a replay
 * slider over the session. Green = bullish pressure (calls bought or puts sold).
 */
const WINDOWS = ["m5", "m15", "m30", "m60"] as const;
const WINDOW_LABELS: Record<(typeof WINDOWS)[number], string> = { m5: "5M", m15: "15M", m30: "30M", m60: "60M" };
const ROWS_EACH_SIDE = 14;
const SESSION_MINUTES = 390;
type Expiration = StrikeFlowResponse["expiration_mode"];
type Levels = { abs: number | null; callWall: number | null; putWall: number | null; flip: number | null };

const compact = new Intl.NumberFormat("es-US", { maximumFractionDigits: 0 });
const formatFlow = (value: number) => (Math.abs(value) < 0.5 ? "·" : compact.format(value));

export function rowLevel(row: StrikeFlowRow): number {
  return row.price ?? row.strike;
}

/** The rows shown: highest strike on top, ROWS_EACH_SIDE above and below the price (all of them when the price is unknown). */
export function visibleRows(rows: StrikeFlowRow[], spot: number | null): StrikeFlowRow[] {
  const sorted = [...rows].sort((a, b) => rowLevel(b) - rowLevel(a));
  if (spot === null) return sorted;
  const above = sorted.filter((row) => rowLevel(row) >= spot).slice(-ROWS_EACH_SIDE);
  const below = sorted.filter((row) => rowLevel(row) < spot).slice(0, ROWS_EACH_SIDE);
  return [...above, ...below];
}

/** The row each level sits on: the nearest visible row, only if it is within one strike step. */
export function levelLabels(rows: StrikeFlowRow[], levels: Levels): Map<number, string[]> {
  const out = new Map<number, string[]>();
  if (rows.length === 0) return out;
  const prices = rows.map(rowLevel);
  const gaps = prices.slice(1).map((p, i) => Math.abs(prices[i] - p)).filter((g) => g > 0).sort((a, b) => a - b);
  const step = gaps.length ? gaps[Math.floor(gaps.length / 2)] : Infinity;
  const entries: [string, number | null][] = [
    ["ABS", levels.abs],
    ["CW", levels.callWall],
    ["PW", levels.putWall],
    ["Flip", levels.flip],
  ];
  for (const [label, value] of entries) {
    if (value === null || !Number.isFinite(value)) continue;
    let best = -1;
    prices.forEach((p, i) => {
      if (best < 0 || Math.abs(p - value) < Math.abs(prices[best] - value)) best = i;
    });
    if (best >= 0 && Math.abs(prices[best] - value) <= step) {
      const key = rows[best].strike;
      out.set(key, [...(out.get(key) ?? []), label]);
    }
  }
  return out;
}

function etClock(iso: string): string {
  return new Date(iso).toLocaleTimeString("es-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false });
}

function minutesSinceOpen(sessionStart: string, now = new Date()): number {
  return Math.max(0, Math.min(SESSION_MINUTES, Math.floor((now.getTime() - new Date(sessionStart).getTime()) / 60_000)));
}

function FlowCell({ value, scale, magnet, title }: { value: number; scale: number; magnet: boolean; title: string }) {
  const width = scale > 0 ? Math.min(100, (Math.abs(value) / scale) * 100) : 0;
  return (
    <td className="v2-chain-cell" title={title}>
      <span className={`v2-chain-bar ${value >= 0 ? "up" : "down"}`} style={{ width: `${width}%` }} />
      <span className="v2-chain-num">
        {magnet ? "🧲 " : ""}
        {formatFlow(value)}
      </span>
    </td>
  );
}

export function FlowChainPanel({ symbol }: { symbol: string }) {
  const { t } = useLanguage();
  const [expiration, setExpiration] = useState<Expiration>("0dte");
  // null = live; otherwise the minute of the session (since 09:30 ET) the replay shows.
  const [replayMinute, setReplayMinute] = useState<number | null>(null);
  const [data, setData] = useState<StrikeFlowResponse | null>(null);
  const [failed, setFailed] = useState(false);
  const [spot, setSpot] = useState<number | null>(null);
  const [levels, setLevels] = useState<Levels>({ abs: null, callWall: null, putWall: null, flip: null });

  useEffect(() => {
    const controller = new AbortController();
    const at =
      replayMinute !== null && data
        ? new Date(new Date(data.session_start).getTime() + replayMinute * 60_000 + 30_000)
        : undefined;
    const load = () =>
      getStrikeFlow(symbol, { expiration, at }, controller.signal)
        .then((response) => {
          setData(response);
          setFailed(false);
        })
        .catch(() => {
          if (!controller.signal.aborted) setFailed(true);
        });
    void load();
    if (replayMinute !== null) return () => controller.abort();
    const interval = window.setInterval(() => void load(), POLLING_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
    // data.session_start only matters for the replay's start; re-running on every poll would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, expiration, replayMinute]);

  useEffect(() => {
    const controller = new AbortController();
    const load = () => {
      getMarket(symbol, controller.signal)
        .then((market) => setSpot(market.price))
        .catch(() => undefined);
      getGamma(symbol, DEFAULT_GAMMA_VIEW, controller.signal)
        .then((gamma) =>
          setLevels({ abs: gamma.absolute_gamma_strike, callWall: gamma.call_wall, putWall: gamma.put_wall, flip: gamma.gamma_flip }),
        )
        .catch(() => undefined);
    };
    load();
    const interval = window.setInterval(load, POLLING_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [symbol]);

  const rows = useMemo(() => visibleRows(data?.strikes ?? [], replayMinute === null ? spot : null), [data, spot, replayMinute]);
  const labels = useMemo(() => levelLabels(rows, levels), [rows, levels]);
  const scales = useMemo(() => {
    const scale = (pick: (row: StrikeFlowRow) => number) => Math.max(0, ...rows.map((row) => Math.abs(pick(row))));
    return {
      m5: scale((r) => r.net.m5),
      m15: scale((r) => r.net.m15),
      m30: scale((r) => r.net.m30),
      m60: scale((r) => r.net.m60),
      day: scale((r) => r.net_day),
    };
  }, [rows]);
  const magnets = useMemo(() => {
    const out: Partial<Record<(typeof WINDOWS)[number], number>> = {};
    for (const w of WINDOWS) {
      const best = rows.reduce<StrikeFlowRow | null>((acc, row) => (acc === null || Math.abs(row.net[w]) > Math.abs(acc.net[w]) ? row : acc), null);
      if (best && Math.abs(best.net[w]) >= 0.5) out[w] = best.strike;
    }
    return out;
  }, [rows]);

  const spotIndex = replayMinute === null && spot !== null ? rows.findIndex((row) => rowLevel(row) < spot) : -1;
  const liveMinutes = data ? minutesSinceOpen(data.session_start) : 0;
  const breakdown = (row: StrikeFlowRow, w: keyof StrikeFlowWindows) =>
    `Calls ${formatFlow(row.calls[w])} · Puts ${formatFlow(row.puts[w])}`;

  return (
    <div className="v2-chain">
      <div className="v2-chain-head">
        <select className="v2-sel" aria-label="Vencimiento" value={expiration} onChange={(e) => setExpiration(e.target.value as Expiration)}>
          {(["0dte", "0-2dte", "all"] as const).map((mode) => (
            <option key={mode} value={mode}>
              {t.flowChain.expiration[mode]}
            </option>
          ))}
        </select>
        {data && <span className={data.net_day >= 0 ? "v2-up" : "v2-down"}>{t.flowChain.netDay(formatFlow(data.net_day))}</span>}
        {data?.signed_share_day != null && <span className="v2-dim">{t.flowChain.signedShare(`${Math.round(data.signed_share_day * 100)} %`)}</span>}
        {data && data.flow_source !== data.symbol && <span className="v2-dim">{t.flowChain.source(data.flow_source)}</span>}
        {data && <span className="v2-dim">{etClock(data.as_of)} ET</span>}
      </div>
      <div className="v2-chain-replay">
        <button type="button" className={replayMinute === null ? "on" : ""} onClick={() => setReplayMinute(null)}>
          {t.flowChain.live}
        </button>
        <input
          type="range"
          min={1}
          max={Math.max(1, liveMinutes)}
          value={replayMinute ?? liveMinutes}
          aria-label="Repetición"
          onChange={(event) => setReplayMinute(Number(event.target.value))}
        />
        {replayMinute !== null && data && (
          <span className="v2-dim">{t.flowChain.replayAt(etClock(new Date(new Date(data.session_start).getTime() + replayMinute * 60_000).toISOString()))}</span>
        )}
      </div>
      {failed && !data ? (
        <p className="v2-panel-note">No se pudieron leer los datos; reintentando.</p>
      ) : !data ? (
        <p className="v2-panel-note">Cargando…</p>
      ) : rows.length === 0 ? (
        <p className="v2-panel-note">{t.flowChain.empty}</p>
      ) : (
        <div className="v2-chain-scroll">
          <table className="v2-chain-table">
            <thead>
              <tr>
                <th>{t.flowChain.strike}</th>
                {WINDOWS.map((w) => (
                  <th key={w}>{WINDOW_LABELS[w]}</th>
                ))}
                <th>{t.flowChain.day}</th>
                <th>{t.flowChain.packages15}</th>
                <th>{t.flowChain.level}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <FlowRow
                  key={row.strike}
                  row={row}
                  showSpotBefore={index === spotIndex}
                  spot={spot}
                  spotLabel={t.flowChain.spot}
                  scales={scales}
                  magnets={magnets}
                  magnetTitle={t.flowChain.magnetTitle}
                  labels={labels.get(row.strike) ?? []}
                  breakdown={breakdown}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="v2-chain-hint">{t.flowChain.hint}</p>
    </div>
  );
}

function FlowRow({
  row,
  showSpotBefore,
  spot,
  spotLabel,
  scales,
  magnets,
  magnetTitle,
  labels,
  breakdown,
}: {
  row: StrikeFlowRow;
  showSpotBefore: boolean;
  spot: number | null;
  spotLabel: string;
  scales: Record<"m5" | "m15" | "m30" | "m60" | "day", number>;
  magnets: Partial<Record<(typeof WINDOWS)[number], number>>;
  magnetTitle: string;
  labels: string[];
  breakdown: (row: StrikeFlowRow, w: keyof StrikeFlowWindows) => string;
}) {
  return (
    <>
      {showSpotBefore && spot !== null && (
        <tr className="v2-chain-spot">
          <td colSpan={8}>
            {spotLabel} {spot.toFixed(2)}
          </td>
        </tr>
      )}
      <tr>
        <td className="v2-chain-strike">
          {rowLevel(row).toFixed(rowLevel(row) % 1 ? 1 : 0)}
          {row.price !== null && row.price !== row.strike && <small> ({row.strike})</small>}
        </td>
        {WINDOWS.map((w) => (
          <FlowCell
            key={w}
            value={row.net[w]}
            scale={scales[w]}
            magnet={magnets[w] === row.strike}
            title={magnets[w] === row.strike ? `${magnetTitle}\n${breakdown(row, w)}` : breakdown(row, w)}
          />
        ))}
        <FlowCell value={row.net_day} scale={scales.day} magnet={false} title={`${row.contracts_signed_day} con lado · ${row.contracts_package_day} en paquetes`} />
        <td className="v2-chain-pkg">{row.packages_15m ? compact.format(row.packages_15m) : "·"}</td>
        <td className="v2-chain-level">
          {labels.map((label) => (
            <span key={label} className={`v2-chain-tag ${label.toLowerCase()}`}>
              {label}
            </span>
          ))}
        </td>
      </tr>
    </>
  );
}
