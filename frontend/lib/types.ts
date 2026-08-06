export type Underlying = {
  symbol: string;
  kind: "equity" | "index";
  is_priority: boolean;
};

export type UnderlyingsResponse = {
  schema_version: number;
  underlyings: Underlying[];
};

export type ScreenerPresetName =
  | "unusual-options-activity"
  | "negative-gamma-board"
  | "max-pain-key-levels"
  | "vanna-exposure-leaders"
  | "charm-decay-pressure";

export type ScreenerPresetResult = {
  symbol: string;
  as_of: string;
  contract: string | null;
  alert_type: "WHALE" | "UNUSUAL" | null;
  amount: number | null;
  net_gamma: number | null;
  gamma_flip: number | null;
  call_wall: number | null;
  put_wall: number | null;
  max_pain: number | null;
  vanna_exposure: number | null;
  charm_exposure: number | null;
};

export type ScreenerPresetResponse = {
  schema_version: number;
  preset: ScreenerPresetName;
  results: ScreenerPresetResult[];
};

export type OptionContract = {
  occ_symbol: string;
  strike: number;
  expiration: string;
  type: "call" | "put";
  bid: number;
  ask: number;
  iv: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  charm: number;
  vanna: number;
  open_interest: number;
  volume: number;
};

export type OptionChainResponse = {
  schema_version: number;
  symbol: string;
  as_of: string;
  spot_price: number;
  contracts: OptionContract[];
};

export type DerivedMetricValue = {
  value: number | null;
  provisional: boolean;
  days_accumulated: number;
};

export type MarketBiasMetric = {
  score: number | null;
  label: "bullish" | "bearish" | "neutral" | null;
  provisional: boolean;
  days_accumulated: number;
};

export type VolatilityRegimeMetric = {
  iv_rank: number | null;
  label: "low" | "moderate" | "high" | null;
  provisional: boolean;
  days_accumulated: number;
};

export type DerivedMetrics = {
  dealer_impact_score: DerivedMetricValue;
  signal_alignment_score: DerivedMetricValue;
  market_bias: MarketBiasMetric;
  volatility_regime: VolatilityRegimeMetric;
};

export type GammaResponse = {
  schema_version: number;
  symbol: string;
  as_of: string;
  gamma_flip: number;
  call_wall: number;
  put_wall: number;
  absolute_gamma_strike: number;
  dealer_position: "long_gamma" | "short_gamma";
  derived_metrics: DerivedMetrics;
};

export type GammaHistoryItem = {
  schema_version: number;
  symbol: string;
  as_of: string;
  gamma_flip: number;
  call_wall: number;
  put_wall: number;
  absolute_gamma_strike: number;
  max_pain: number;
  net_gamma: number;
  vega_exposure: number;
  theta_exposure: number;
  charm_exposure: number;
  vanna_exposure: number;
  dealer_position: "long_gamma" | "short_gamma";
};

export type GammaHistoryResponse = {
  schema_version: number;
  symbol: string;
  items: GammaHistoryItem[];
};

export type MarketResponse = {
  schema_version: number;
  symbol: string;
  as_of: string;
  price: number;
  volume: number;
  dealer_mode: "long_gamma" | "short_gamma";
  dealer_mode_source: "agree" | "price_vs_flip";
  dealer_mode_confirmed: boolean;
  expected_move?: ExpectedMove;
};

export type ExpectedMove = {
  implied_1sd_dollars: number;
  implied_1sd_pct: number;
  remaining_1sd_dollars: number;
  remaining_1sd_pct: number;
  upper_bound: number;
  lower_bound: number;
  atm_iv: number;
};
