import type { DerivedMetrics } from "@/lib/types";

export const derivedMetricsFixture: DerivedMetrics = {
  dealer_impact_score: { value: 78, provisional: false, days_accumulated: 60 },
  signal_alignment_score: { value: 62, provisional: false, days_accumulated: 60 },
  market_bias: {
    score: 71.2,
    label: "bullish",
    provisional: false,
    days_accumulated: 60,
  },
  volatility_regime: {
    iv_rank: 42.5,
    label: "moderate",
    provisional: false,
    days_accumulated: 60,
  },
};
