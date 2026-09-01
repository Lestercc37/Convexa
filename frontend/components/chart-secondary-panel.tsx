"use client";

import { useEffect, useMemo, useState } from "react";
import { getAlerts, getGammaProfile } from "@/lib/api";
import { describeError } from "@/lib/i18n/describe-error";
import { useLanguage } from "@/lib/i18n/language-context";
import { POLLING_INTERVAL_MS } from "@/lib/polling";
import type { GammaAggregateItem, GammaAggregateResponse, WhaleAlert } from "@/lib/types";

type ChartSecondaryPanelProps = { symbol: string };
type SecondaryView = "gex" | "flow";

// Only up to ~3-4 rows ever render here (near-the-money strike range,
// both MockDataProvider and ThetaDataProvider) — this panel is a fixed
// ~17% share of .tv-center's height (dashboard-spec.md section 22,
// ~172px/122px measured at 1920x1080/1366x768), nowhere near enough for
// pre-session-panel.tsx's full-size GEX Profile. Same horizontal
// proportions as that chart (reused for visual consistency across the
// app), just compressed vertically.
const GEX_PLOT = { top: 6, bottom: 94, centerLeft: 120, centerRight: 640, edgeLeft: 20, edgeRight: 740 };
const FLOW_PLOT = { top: 10, bottom: 80, left: 20, right: 740 };

const level = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

function alertKey(alert: WhaleAlert) {
  return `${alert.symbol}-${alert.contract}-${alert.timestamp}`;
}

function scale(value: number, minimum: number, maximum: number, start: number, end: number) {
  if (maximum <= minimum) return (start + end) / 2;
  return start + ((value - minimum) / (maximum - minimum)) * (end - start);
}

function magnitude(value: number, peak: number, start: number, end: number) {
  if (peak <= 0) return start;
  const ratio = Math.min(1, Math.abs(value) / peak);
  return start + ratio * (end - start);
}

export function ChartSecondaryPanel({ symbol }: ChartSecondaryPanelProps) {
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
    () => [...(profile?.items ?? [])].sort((a, b) => b.strike - a.strike),
    [profile],
  );
  const gexPeak = useMemo(
    () =>
      gexItems.reduce(
        (max, item) => Math.max(max, Math.abs(item.call_gamma_exposure), Math.abs(item.put_gamma_exposure)),
        0,
      ),
    [gexItems],
  );
  const gexStrikes = gexItems.map((item) => item.strike);
  const gexMinStrike = gexStrikes.length ? Math.min(...gexStrikes) : 0;
  const gexMaxStrike = gexStrikes.length ? Math.max(...gexStrikes) : 0;
  const gexY = (strike: number) => scale(strike, gexMinStrike, gexMaxStrike, GEX_PLOT.bottom, GEX_PLOT.top);
  const gexRowHeight =
    gexItems.length > 1 ? (GEX_PLOT.bottom - GEX_PLOT.top) / (gexItems.length - 1) : GEX_PLOT.bottom - GEX_PLOT.top;
  const gexBarHeight = Math.min(18, Math.max(4, gexRowHeight * 0.6));

  // Sorted ascending by time (Map insertion order isn't chronological once
  // two polls' responses interleave) and re-summed from scratch on every
  // change — simpler and safer than an incremental running total, and
  // cheap given the 1000-alert cap.
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
  const flowX = (timestamp: string) => scale(Date.parse(timestamp), flowMinTime, flowMaxTime, FLOW_PLOT.left, FLOW_PLOT.right);
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
              className="secondary-gex-axis"
              x1={GEX_PLOT.centerLeft}
              y1={GEX_PLOT.top}
              x2={GEX_PLOT.centerLeft}
              y2={GEX_PLOT.bottom}
            />
            {gexItems.map((item) => (
              <g key={item.strike} aria-label={`Strike ${item.strike}`}>
                <rect
                  className="secondary-gex-bar call"
                  x={GEX_PLOT.centerLeft}
                  y={gexY(item.strike) - gexBarHeight / 2}
                  width={magnitude(item.call_gamma_exposure, gexPeak, 0, GEX_PLOT.centerRight - GEX_PLOT.centerLeft)}
                  height={gexBarHeight}
                />
                <rect
                  className="secondary-gex-bar put"
                  x={
                    GEX_PLOT.centerLeft -
                    magnitude(item.put_gamma_exposure, gexPeak, 0, GEX_PLOT.centerLeft - GEX_PLOT.edgeLeft)
                  }
                  y={gexY(item.strike) - gexBarHeight / 2}
                  width={magnitude(item.put_gamma_exposure, gexPeak, 0, GEX_PLOT.centerLeft - GEX_PLOT.edgeLeft)}
                  height={gexBarHeight}
                />
                <text className="secondary-gex-strike-label" x={GEX_PLOT.edgeRight + 4} y={gexY(item.strike) + 4}>
                  {level.format(item.strike)}
                </text>
              </g>
            ))}
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
