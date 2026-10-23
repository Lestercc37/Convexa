import { useLanguage } from "@/lib/i18n/language-context";
import type { ExpectedMove } from "@/lib/types";

const twoDecimals = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// The expected move as a percentage in the top bar, next to the gamma regime pill. The old Expected Move panel left with the
// Dealers dock clean-up of #311 (2026-10-18) and the percentage was shown nowhere since; Lester wants it in sight (2026-10-23).
// It is the same 1-sigma move the chart's EMC / EMP lines are drawn from (market.expected_move.implied_1sd_*: anchored at the
// session open, shrinking with the time left to the close for 0DTE), so the pill and the lines can never disagree. The tooltip
// carries the points, the range and the equivalent IV (atm_iv is a fraction).
export function ExpectedMoveCompactBadge({ expectedMove }: { expectedMove?: ExpectedMove }) {
  const { t } = useLanguage();
  if (!expectedMove || !(expectedMove.implied_1sd_pct > 0)) return null;
  const pct = twoDecimals.format(expectedMove.implied_1sd_pct);
  const points = twoDecimals.format(expectedMove.implied_1sd_dollars);
  const title = t.expectedMoveBadge.title(
    points,
    pct,
    twoDecimals.format(expectedMove.lower_bound),
    twoDecimals.format(expectedMove.upper_bound),
    twoDecimals.format(expectedMove.atm_iv * 100),
  );
  return (
    <span className="mode-pill expected-move-compact" title={title} aria-label={title}>
      {t.expectedMoveBadge.label} ±{pct}%<span className="expected-move-compact-points">±{points}</span>
    </span>
  );
}
