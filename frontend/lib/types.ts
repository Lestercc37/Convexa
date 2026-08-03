export type Underlying = {
  symbol: string;
  kind: "equity" | "index";
  is_priority: boolean;
};

export type UnderlyingsResponse = {
  schema_version: number;
  underlyings: Underlying[];
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
};
