"use client";

import { useEffect, useMemo, useState } from "react";
import { getGammaProfile } from "@/lib/api";
import { describeError } from "@/lib/i18n/describe-error";
import { useLanguage, type Language } from "@/lib/i18n/language-context";
import type { GammaAggregateItem, GammaAggregateResponse } from "@/lib/types";

type PreSessionPanelProps = { symbol: string };

const PLOT = { top: 20, bottom: 320, centerLeft: 120, centerRight: 640, edgeLeft: 20, edgeRight: 740 };

const level = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

const DATE_LOCALE: Record<Language, string> = { es: "es-US", en: "en-US" };

function closeDateFormatter(language: Language): Intl.DateTimeFormat {
  return new Intl.DateTimeFormat(DATE_LOCALE[language], {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
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

export function PreSessionPanel({ symbol }: PreSessionPanelProps) {
  const { language, t } = useLanguage();
  const [profile, setProfile] = useState<GammaAggregateResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const closeDateLabel = useMemo(() => closeDateFormatter(language), [language]);

  useEffect(() => {
    if (!symbol) return;
    const controller = new AbortController();
    // Fetched once per symbol, on purpose — this is the frozen snapshot from
    // the previous close (dashboard-spec.md section 8), not a live view, so
    // it never joins the Dashboard's 30s polling loop.
    getGammaProfile(symbol, controller.signal)
      .then((response) => setProfile(response))
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason);
        }
      });
    return () => controller.abort();
  }, [symbol]);

  const items = useMemo<GammaAggregateItem[]>(
    () => [...(profile?.items ?? [])].sort((a, b) => b.strike - a.strike),
    [profile],
  );

  const peak = useMemo(
    () =>
      items.reduce(
        (max, item) => Math.max(max, Math.abs(item.call_gamma_exposure), Math.abs(item.put_gamma_exposure)),
        0,
      ),
    [items],
  );

  const strikes = items.map((item) => item.strike);
  const minStrike = strikes.length ? Math.min(...strikes) : 0;
  const maxStrike = strikes.length ? Math.max(...strikes) : 0;
  const y = (strike: number) => scale(strike, minStrike, maxStrike, PLOT.bottom, PLOT.top);
  const rowHeight = items.length > 1 ? (PLOT.bottom - PLOT.top) / (items.length - 1) : PLOT.bottom - PLOT.top;
  const barHeight = Math.min(18, Math.max(4, rowHeight * 0.6));

  return (
    <section className="panel pre-session-panel" aria-labelledby="pre-session-title">
      <div className="panel-heading pre-session-heading">
        <div>
          <p className="eyebrow">{t.preSessionPanel.eyebrow}</p>
          <h2 id="pre-session-title">{t.preSessionPanel.title(symbol)}</h2>
        </div>
        {profile && (
          <span className="frozen-pill" role="status">
            {t.preSessionPanel.frozenPill(closeDateLabel.format(new Date(profile.as_of)))}
          </span>
        )}
      </div>

      {error ? (
        <p className="pre-session-status error" role="alert">
          {describeError(error, t)}
        </p>
      ) : profile && items.length ? (
        <div className="pre-session-chart-wrap">
          <svg
            className="pre-session-chart"
            viewBox="0 0 760 340"
            role="img"
            aria-label={t.preSessionPanel.chartAriaLabel(
              symbol,
              closeDateLabel.format(new Date(profile.as_of)),
            )}
          >
            <line
              className="pre-session-axis"
              x1={PLOT.centerLeft}
              y1={PLOT.top}
              x2={PLOT.centerLeft}
              y2={PLOT.bottom}
            />

            {items.map((item) => (
              <g key={item.strike} aria-label={`Strike ${item.strike}`}>
                <rect
                  className="pre-session-bar call"
                  x={PLOT.centerLeft}
                  y={y(item.strike) - barHeight / 2}
                  width={magnitude(item.call_gamma_exposure, peak, 0, PLOT.centerRight - PLOT.centerLeft)}
                  height={barHeight}
                />
                <rect
                  className="pre-session-bar put"
                  x={
                    PLOT.centerLeft -
                    magnitude(item.put_gamma_exposure, peak, 0, PLOT.centerLeft - PLOT.edgeLeft)
                  }
                  y={y(item.strike) - barHeight / 2}
                  width={magnitude(item.put_gamma_exposure, peak, 0, PLOT.centerLeft - PLOT.edgeLeft)}
                  height={barHeight}
                />
                <text className="pre-session-strike-label" x={PLOT.edgeRight + 4} y={y(item.strike) + 4}>
                  {level.format(item.strike)}
                </text>
              </g>
            ))}

            <g aria-label={`Gamma Flip ${profile.gamma_flip}`}>
              <line
                className="pre-session-reference flip"
                x1={PLOT.edgeLeft}
                y1={y(profile.gamma_flip)}
                x2={PLOT.edgeRight}
                y2={y(profile.gamma_flip)}
              />
              <text className="pre-session-reference-label flip" x={PLOT.edgeLeft} y={y(profile.gamma_flip) - 6}>
                Gamma Flip {level.format(profile.gamma_flip)}
              </text>
            </g>
            <g aria-label={`Max Pain ${profile.max_pain}`}>
              <line
                className="pre-session-reference pain"
                x1={PLOT.edgeLeft}
                y1={y(profile.max_pain)}
                x2={PLOT.edgeRight}
                y2={y(profile.max_pain)}
              />
              <text className="pre-session-reference-label pain" x={PLOT.edgeLeft} y={y(profile.max_pain) - 6}>
                Max Pain {level.format(profile.max_pain)}
              </text>
            </g>
          </svg>
          <div className="pre-session-legend" aria-label={t.preSessionPanel.legendAriaLabel}>
            <span>
              <i className="legend-dot call" /> Calls
            </span>
            <span>
              <i className="legend-dot put" /> Puts
            </span>
          </div>
        </div>
      ) : profile ? (
        <p className="pre-session-status">{t.preSessionPanel.noBreakdown}</p>
      ) : (
        <p className="pre-session-status">{t.preSessionPanel.loading}</p>
      )}
    </section>
  );
}
