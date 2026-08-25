export type Underlying = {
  symbol: string;
  kind: "equity" | "index" | "future";
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
  max_pain: number;
  net_gamma: number;
  vega_exposure: number;
  theta_exposure: number;
  charm_exposure: number;
  vanna_exposure: number;
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

export type GammaAggregateItem = {
  strike: number;
  total_gamma_exposure: number;
  call_gamma_exposure: number;
  put_gamma_exposure: number;
  net_gamma: number;
  contract_count: number;
  absolute_gamma: number;
};

export type GammaAggregateResponse = {
  schema_version: number;
  symbol: string;
  as_of: string;
  gamma_flip: number;
  max_pain: number;
  total_market_gamma: number;
  positive_gamma: number;
  negative_gamma: number;
  absolute_gamma_strike: number;
  peak_gamma_value: number;
  items: GammaAggregateItem[];
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
  anchored_vwap?: AnchoredVwap;
  atr_range?: AtrRange;
  closing_dynamics?: ClosingDynamics;
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

export type AnchoredVwap = {
  value: number | null;
  provisional: boolean;
  anchor_time: string;
  sample_count: number;
};

export type AtrRange = {
  atr: number | null;
  atr_provisional: boolean;
  daily_bars_count: number;
  today_open: number | null;
  bands_provisional: boolean;
  outer_upper_band: number | null;
  outer_lower_band: number | null;
  inner_upper_band: number | null;
  inner_lower_band: number | null;
};

export type ClosingDynamics = {
  active: boolean;
  time_to_close_pct: number;
  pin_score: number;
  magnet_strike: number | null;
  charm_regime: "time_decay_dealers_buy" | "time_decay_dealers_sell" | null;
  vanna_interpretation: "iv_increase_dealers_buy" | "iv_increase_dealers_sell" | null;
  max_pain: number;
};

export type WhaleAlert = {
  symbol: string;
  contract: string;
  type: "WHALE" | "UNUSUAL" | "SUSTAINED_FLOW";
  amount: number;
  timestamp: string;
  // Bulk Volume Classification estimates (Easley, López de Prado, O'Hara
  // 2012) — derived from price movement alone, never confirmed
  // buy/sell-side order flow.
  estimated_buy_volume: number;
  estimated_sell_volume: number;
};

export type WhaleAlertsResponse = {
  schema_version: number;
  symbol: string;
  alerts: WhaleAlert[];
};

export type WhaleThreshold = {
  symbol: string;
  unusual_min: number;
  whale_min: number;
  unusual_multiplier: number;
  whale_multiplier: number;
  sustained_flow_min: number;
};

export type WhaleThresholdsResponse = {
  schema_version: number;
  thresholds: WhaleThreshold[];
};

export type WhaleThresholdUpdate = {
  unusual_min: number;
  whale_min: number;
  unusual_multiplier: number;
  whale_multiplier: number;
  sustained_flow_min: number;
};
