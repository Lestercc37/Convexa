import { useLanguage } from "@/lib/i18n/language-context";
import type { Translations } from "@/lib/i18n/translations";
import type { ClosingDynamics } from "@/lib/types";

type ClosingDynamicsPanelProps = {
  closingDynamics?: ClosingDynamics;
};

// Last ~5% of the session (~20 of the ~390 minute session) — a purely
// presentational emphasis threshold (border/background intensify), not a
// new activation gate. `active` (dashboard-spec.md section 9,
// time_to_close_pct <= 15) already decides whether this panel renders at
// all; this only decides how loudly it renders once it does.
const URGENT_TIME_TO_CLOSE_PCT = 5;

const level = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

function charmRegimeLabel(
  charmRegime: ClosingDynamics["charm_regime"],
  t: Translations,
): string {
  switch (charmRegime) {
    case "time_decay_dealers_buy":
      return t.closingDynamicsPanel.charmTimeDecayBuy;
    case "time_decay_dealers_sell":
      return t.closingDynamicsPanel.charmTimeDecaySell;
    default:
      return t.closingDynamicsPanel.charmNeutral;
  }
}

function vannaInterpretationLabel(
  vannaInterpretation: ClosingDynamics["vanna_interpretation"],
  t: Translations,
): string {
  switch (vannaInterpretation) {
    case "iv_increase_dealers_buy":
      return t.closingDynamicsPanel.vannaIvIncreaseBuy;
    case "iv_increase_dealers_sell":
      return t.closingDynamicsPanel.vannaIvIncreaseSell;
    default:
      return t.closingDynamicsPanel.vannaNeutral;
  }
}

function regimeTone(
  value: ClosingDynamics["charm_regime"] | ClosingDynamics["vanna_interpretation"],
): "buy" | "sell" | "neutral" {
  if (value === "time_decay_dealers_buy" || value === "iv_increase_dealers_buy") return "buy";
  if (value === "time_decay_dealers_sell" || value === "iv_increase_dealers_sell") return "sell";
  return "neutral";
}

export function ClosingDynamicsPanel({ closingDynamics }: ClosingDynamicsPanelProps) {
  const { t } = useLanguage();
  // Conditional by design, not a toggle (dashboard-spec.md section 9): the
  // panel is absent outside the closing window, no empty state, no
  // "unavailable" message — `active` is the real backend signal, not a
  // null check (`closing_dynamics` is always present on the response).
  if (!closingDynamics || !closingDynamics.active) return null;

  const { pin_score, magnet_strike, charm_regime, vanna_interpretation, time_to_close_pct } =
    closingDynamics;
  const pinScorePct = Math.max(0, Math.min(100, pin_score));
  const urgent = time_to_close_pct <= URGENT_TIME_TO_CLOSE_PCT;

  return (
    <section
      className={`panel closing-dynamics-panel${urgent ? " urgent" : ""}`}
      aria-label={t.closingDynamicsPanel.ariaLabel}
    >
      <p className="eyebrow">{t.closingDynamicsPanel.eyebrow}</p>

      <div className="closing-dynamics-row">
        <span className="closing-dynamics-label">{t.closingDynamicsPanel.pinRiskScoreLabel}</span>
        <strong className="closing-dynamics-value">{level.format(pin_score)}</strong>
      </div>
      <div
        className="pin-score-meter"
        role="meter"
        aria-label={t.closingDynamicsPanel.pinRiskScoreLabel}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pinScorePct}
      >
        <div className="pin-score-meter-fill" style={{ width: `${pinScorePct}%` }} />
      </div>
      <p className="closing-dynamics-note">{t.common.convexaNote}</p>

      <div className="closing-dynamics-row">
        <span className="closing-dynamics-label">{t.closingDynamicsPanel.magnetStrikeLabel}</span>
        <strong className="closing-dynamics-value">
          {magnet_strike === null ? "—" : level.format(magnet_strike)}
        </strong>
      </div>

      <p className={`closing-dynamics-regime ${regimeTone(charm_regime)}`}>
        {charmRegimeLabel(charm_regime, t)}
      </p>
      <p className={`closing-dynamics-regime ${regimeTone(vanna_interpretation)}`}>
        {vannaInterpretationLabel(vanna_interpretation, t)}
      </p>
    </section>
  );
}
