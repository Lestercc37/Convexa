"use client";

import { useEffect, useMemo, useState } from "react";
import { getAlerts, getGammaProfile } from "@/lib/api";
import { describeError } from "@/lib/i18n/describe-error";
import { useLanguage } from "@/lib/i18n/language-context";
import { POLLING_INTERVAL_MS } from "@/lib/polling";
import type { GammaAggregateItem, GammaAggregateResponse, WhaleAlert } from "@/lib/types";

type ChartSecondaryPanelProps = { symbol: string; spotPrice: number };
type SecondaryView = "gex" | "flow";

// SpotGamma-style vertical profile: strike on the X axis, exposure
// magnitude on the Y axis, calls above the zero line and puts below it
// (call_gamma_exposure/put_gamma_exposure already carry that sign —
// dealer positioning convention baked in at calculation time, see
// backend/adapters/providers/mock/gamma_exposure.py — no sign-flip
// needed here). `labelY` sits below `bottom` so strike labels never
// overlap a put bar reaching the floor of the plot.
const GEX_PLOT = { left: 30, right: 740, top: 8, bottom: 74, labelY: 92 };
// A non-zero exposure always stays visually present even when the
// opposite side completely dominates the shared scale (see gexY below)
// — legible without pretending both sides are equally sized.
const GEX_MIN_BAR_HEIGHT = 2;
// Empirical per-character width at the strike label's 11px font
// (.secondary-gex-strike-label in globals.css), plus a small gap so
// adjacent labels never touch even at their estimated worst case.
const GEX_LABEL_CHAR_WIDTH = 6.5;
const GEX_LABEL_GAP = 6;
const FLOW_PLOT = { top: 10, bottom: 80, left: 20, right: 740 };

const level = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

// Same fix as alerts-panel.tsx's own alertKey() -- contract+timestamp
// alone collides whenever a single reading trips both a magnitude
// threshold (WHALE/UNUSUAL) and the separate sustained-flow window at
// the same as_of. Used as a Map key below (`alertsByKey`), so this
// wasn't just a React warning here -- the second alert silently
// overwrote the first in the Map with no error at all.
function alertKey(alert: WhaleAlert) {
  return `${alert.symbol}-${alert.contract}-${alert.timestamp}-${alert.type}`;
}

function scale(value: number, minimum: number, maximum: number, start: number, end: number) {
  if (maximum <= minimum) return (start + end) / 2;
  return start + ((value - minimum) / (maximum - minimum)) * (end - start);
}

export function ChartSecondaryPanel({ symbol, spotPrice }: ChartSecondaryPanelProps) {
  const { t } = useLanguage();
  const [view, setView] = useState<SecondaryView>("gex");
  const [profile, setProfile] = useState<GammaAggregateResponse | null>(null);
  const [profileError, setProfileError] = useState<unknown>(null);
  // Keyed by alertKey (same dedup identity alerts-panel.tsx already uses)
  // so repeated polls merge into one running set instead of replacing it
  // — this is what makes the flow "accumulated" rather than a snapshot of
  // whatever the backend's most recent ~1000-alert window happens to
  // hold. See the honest "Datos desde..." caption below for why this is
  // still best-effort, not a guaranteed full session.
  const [alertsByKey, setAlertsByKey] = useState<Map<string, WhaleAlert>>(new Map());
  const [flowError, setFlowError] = useState<unknown>(null);
  const [hasFetchedAlerts, setHasFetchedAlerts] = useState(false);

  useEffect(() => {
    if (!symbol) return;
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const response = await getGammaProfile(symbol, controller.signal);
        setProfile(response);
        setProfileError(null);
      } catch (reason: unknown) {
        if (!controller.signal.aborted) setProfileError(reason);
      }
    };
    void refresh();
    const interval = window.setInterval(() => void refresh(), POLLING_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [symbol]);

  useEffect(() => {
    if (!symbol) return;
    const controller = new AbortController();
    const refresh = async () => {
      try {
        // The largest limit the backend allows (`le=1000`) — the shared,
        // in-memory, non-persisted alert history this reads from is
        // itself capped at 1000 across every symbol, so this only
        // reduces (never eliminates) the chance of missing an alert
        // between polls. See docs/dashboard-spec.md for the documented
        // limitation and the honest caption this renders below.
        const response = await getAlerts(symbol, controller.signal, 1000);
        setAlertsByKey((current) => {
          const next = new Map(current);
          for (const alert of response.alerts) next.set(alertKey(alert), alert);
          return next;
        });
        setFlowError(null);
      } catch (reason: unknown) {
        if (!controller.signal.aborted) setFlowError(reason);
      } finally {
        if (!controller.signal.aborted) setHasFetchedAlerts(true);
      }
    };
    void refresh();
    const interval = window.setInterval(() => void refresh(), POLLING_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [symbol]);

  const gexItems = useMemo<GammaAggregateItem[]>(
    () => [...(profile?.items ?? [])].sort((a, b) => a.strike - b.strike),
    [profile],
  );
  const gexStrikes = gexItems.map((item) => item.strike);
  const gexMinStrike = gexStrikes.length ? Math.min(...gexStrikes) : 0;
  const gexMaxStrike = gexStrikes.length ? Math.max(...gexStrikes) : 0;
  // Padding on both sides of the strike range so the outermost bars (and
  // the spot price line, if it sits right at an edge strike) aren't
  // flush against the plot border.
  const gexStrikePadding = (gexMaxStrike - gexMinStrike || 1) * 0.2;
  const gexXMin = gexMinStrike - gexStrikePadding;
  const gexXMax = gexMaxStrike + gexStrikePadding;
  const gexX = (strike: number) => scale(strike, gexXMin, gexXMax, GEX_PLOT.left, GEX_PLOT.right);
  const gexBarWidth = gexItems.length
    ? Math.min(56, ((GEX_PLOT.right - GEX_PLOT.left) / gexItems.length) * 0.55)
    : 0;

  // One shared linear scale for both directions (not independently
  // normalized per side) — if calls dominate 10x over puts, calls
  // genuinely occupy ~91% of the vertical space and puts ~9%, truthfully
  // representing the imbalance instead of making both sides look equally
  // tall. GEX_MIN_BAR_HEIGHT below is what keeps the dominated side from
  // visually disappearing.
  const gexMaxCall = Math.max(0, ...gexItems.map((item) => item.call_gamma_exposure));
  const gexMinPut = Math.min(0, ...gexItems.map((item) => item.put_gamma_exposure));
  const gexY = (value: number) => scale(value, gexMinPut, gexMaxCall, GEX_PLOT.bottom, GEX_PLOT.top);
  const gexZeroY = gexY(0);

  function gexBarRect(value: number): { y: number; height: number } {
    if (value === 0) return { y: gexZeroY, height: 0 };
    if (value > 0) {
      const height = Math.max(gexZeroY - gexY(value), GEX_MIN_BAR_HEIGHT);
      return { y: gexZeroY - height, height };
    }
    const height = Math.max(gexY(value) - gexZeroY, GEX_MIN_BAR_HEIGHT);
    return { y: gexZeroY, height };
  }

  // Clamped to the visible plot — the live spot price can legitimately
  // sit outside the current near-the-money strike range for a poll or
  // two (e.g. price moved since the chain was last fetched), and the
  // line should stay readable at the edge instead of drifting off-canvas.
  const gexSpotX = gexItems.length
    ? Math.min(GEX_PLOT.right, Math.max(GEX_PLOT.left, gexX(spotPrice)))
    : 0;

  // Dynamic label thinning — the ATR-anchored width (docs/use-cases.md)
  // can bring anywhere from 3 strikes (most symbols) to 30+ (SPX at its
  // $5 spacing), and every strike used to get its own label
  // unconditionally, which overlapped badly once that count grew past
  // what the plot's width can legibly hold. Sized from the *longest*
  // formatted strike currently on screen (a wide symbol like SPX needs
  // more room per label than a 3-digit one), not a fixed character
  // count, so it self-adjusts to whichever symbol/dataset is showing —
  // recomputed fresh from the real data every render, not tuned to one
  // symbol in particular.
  const gexLongestLabelLength = gexItems.length
    ? Math.max(...gexItems.map((item) => level.format(item.strike).length))
    : 0;
  const gexLabelWidth = gexLongestLabelLength * GEX_LABEL_CHAR_WIDTH + GEX_LABEL_GAP;
  const gexMaxVisibleLabels =
    gexLabelWidth > 0
      ? Math.max(1, Math.floor((GEX_PLOT.right - GEX_PLOT.left) / gexLabelWidth))
      : gexItems.length;
  const gexLabelStep = gexItems.length
    ? Math.max(1, Math.ceil(gexItems.length / gexMaxVisibleLabels))
    : 1;
  // The strike nearest the live spot is always labeled regardless of the
  // step pattern above — the single most relevant reference point on
  // this chart should never be the one silently skipped.
  const gexNearestToSpotIndex = gexItems.reduce(
    (nearest, item, index) =>
      Math.abs(item.strike - spotPrice) < Math.abs(gexItems[nearest].strike - spotPrice)
        ? index
        : nearest,
    0,
  );

  // A step-pattern index within one step of the forced nearest-to-spot
  // label collides with it (verified live against a real SPX chain,
  // not just estimated: the forced label and its immediate step-pattern
  // neighbor measured ~8px of actual overlap) — suppress that neighbor
  // instead of showing both. The forced label already covers that part
  // of the axis, so nothing is lost.
  function gexShowLabel(index: number): boolean {
    if (index === gexNearestToSpotIndex) return true;
    if (index % gexLabelStep !== 0) return false;
    return Math.abs(index - gexNearestToSpotIndex) >= gexLabelStep;
  }

  const flowPoints = useMemo(() => {
    const sorted = [...alertsByKey.values()].sort(
      (left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp),
    );
    return sorted.reduce<{ timestamp: string; value: number }[]>((points, alert) => {
      const previous = points.at(-1)?.value ?? 0;
      const value = previous + alert.estimated_buy_volume - alert.estimated_sell_volume;
      points.push({ timestamp: alert.timestamp, value });
      return points;
    }, []);
  }, [alertsByKey]);

  const flowValues = flowPoints.map((point) => point.value);
  const flowMinValue = flowValues.length ? Math.min(0, ...flowValues) : -1;
  const flowMaxValue = flowValues.length ? Math.max(0, ...flowValues) : 1;
  const flowMinTime = flowPoints.length ? Date.parse(flowPoints[0].timestamp) : 0;
  const flowMaxTime = flowPoints.length ? Date.parse(flowPoints.at(-1)!.timestamp) : 0;
  const flowX = (timestamp: string) =>
    scale(Date.parse(timestamp), flowMinTime, flowMaxTime, FLOW_PLOT.left, FLOW_PLOT.right);
  const flowYForValue = (value: number) => scale(value, flowMinValue, flowMaxValue, FLOW_PLOT.bottom, FLOW_PLOT.top);
  const flowZeroY = flowYForValue(0);
  const flowPath = flowPoints.map((point) => `${flowX(point.timestamp)},${flowYForValue(point.value)}`).join(" ");
  const flowIsPositive = (flowPoints.at(-1)?.value ?? 0) >= 0;
  const flowSinceTime = flowPoints.length
    ? new Date(flowPoints[0].timestamp).toLocaleTimeString()
    : null;

  return (
    <section className="chart-secondary-panel" aria-label={t.chartSecondaryPanel.ariaLabel}>
      <div className="chart-secondary-heading">
        <p className="eyebrow">{t.chartSecondaryPanel.eyebrow}</p>
        <div
          className="chart-secondary-toggle"
          role="group"
          aria-label={t.chartSecondaryPanel.toggleGroupAriaLabel}
        >
          <button type="button" aria-pressed={view === "gex"} onClick={() => setView("gex")}>
            {t.chartSecondaryPanel.gexToggleButton}
          </button>
          <button type="button" aria-pressed={view === "flow"} onClick={() => setView("flow")}>
            {t.chartSecondaryPanel.flowToggleButton}
          </button>
        </div>
      </div>

      {view === "gex" ? (
        profileError ? (
          <p className="chart-secondary-status error" role="alert">
            {describeError(profileError, t)}
          </p>
        ) : profile && gexItems.length ? (
          <svg
            className="secondary-gex-chart"
            viewBox="0 0 760 100"
            role="img"
            aria-label={t.chartSecondaryPanel.gexChartAriaLabel(symbol)}
          >
            <line
              className="secondary-gex-zero"
              x1={GEX_PLOT.left}
              y1={gexZeroY}
              x2={GEX_PLOT.right}
              y2={gexZeroY}
            />
            {gexItems.map((item, index) => {
              const callRect = gexBarRect(item.call_gamma_exposure);
              const putRect = gexBarRect(item.put_gamma_exposure);
              const x = gexX(item.strike) - gexBarWidth / 2;
              // Bars always render for every strike — only the text
              // label is thinned, never the data itself.
              const showLabel = gexShowLabel(index);
              return (
                <g key={item.strike} aria-label={`Strike ${item.strike}`}>
                  <rect
                    className="secondary-gex-bar call"
                    x={x}
                    y={callRect.y}
                    width={gexBarWidth}
                    height={callRect.height}
                  />
                  <rect
                    className="secondary-gex-bar put"
                    x={x}
                    y={putRect.y}
                    width={gexBarWidth}
                    height={putRect.height}
                  />
                  {showLabel && (
                    <text
                      className="secondary-gex-strike-label"
                      x={gexX(item.strike)}
                      y={GEX_PLOT.labelY}
                    >
                      {level.format(item.strike)}
                    </text>
                  )}
                </g>
              );
            })}
            <g aria-label={t.chartSecondaryPanel.gexSpotPriceAriaLabel(level.format(spotPrice))}>
              <line
                className="secondary-gex-spot"
                x1={gexSpotX}
                y1={GEX_PLOT.top}
                x2={gexSpotX}
                y2={GEX_PLOT.bottom}
              />
              <text className="secondary-gex-spot-label" x={gexSpotX} y={GEX_PLOT.top - 1}>
                {level.format(spotPrice)}
              </text>
            </g>
          </svg>
        ) : profile ? (
          <p className="chart-secondary-status">{t.chartSecondaryPanel.gexNoBreakdown}</p>
        ) : (
          <p className="chart-secondary-status">{t.chartSecondaryPanel.gexLoading}</p>
        )
      ) : flowError ? (
        <p className="chart-secondary-status error" role="alert">
          {describeError(flowError, t)}
        </p>
      ) : flowPoints.length ? (
        <>
          <svg
            className="secondary-flow-chart"
            viewBox="0 0 760 90"
            role="img"
            aria-label={t.chartSecondaryPanel.flowChartAriaLabel(symbol)}
          >
            <line
              className="secondary-flow-zero"
              x1={FLOW_PLOT.left}
              y1={flowZeroY}
              x2={FLOW_PLOT.right}
              y2={flowZeroY}
            />
            <polyline
              className={`secondary-flow-line ${flowIsPositive ? "buy" : "sell"}`}
              points={flowPath}
            />
          </svg>
          {flowSinceTime && (
            <p className="chart-secondary-caption">
              {t.chartSecondaryPanel.flowSinceLabel(flowSinceTime)}
            </p>
          )}
        </>
      ) : hasFetchedAlerts ? (
        <p className="chart-secondary-status">{t.chartSecondaryPanel.flowEmpty}</p>
      ) : (
        <p className="chart-secondary-status">{t.chartSecondaryPanel.flowLoading}</p>
      )}
    </section>
  );
}
