import type { Translations } from "./translations";

export const en: Translations = {
  common: {
    languageSwitcherAriaLabel: "Language",
    convexaNote: "Convexa's own metric, not a market standard",
  },
  errors: {
    notFound: "The requested resource wasn't found.",
    requestFailed: "The request couldn't be completed. Please try again.",
  },
  dashboard: {
    underlyingLabel: "Underlying",
    timeframeGroupAriaLabel: "Timeframe",
    viewGroupAriaLabel: "View",
    liveButton: "Live",
    preSessionButton: "Pre-Session",
    exposureGroupAriaLabel: "Charm and Vanna Exposure",
    aggregatedGreeksEyebrow: "Aggregated Greeks",
    loadingRegime: "Loading regime and levels…",
    settingsButtonAriaLabel: "Whale Alerts threshold settings",
    resizeSeparatorAriaLabel: "Resize panels",
  },
  regimeBadge: {
    ariaLabel: "Gamma regime",
    currentRegimeEyebrow: "Current regime",
    transientAriaLabel: "Transient regime",
    unconfirmedTooltip:
      "Price crossed the Gamma Flip before the last aggregate recalculation — regime based on price.",
    above: "above",
    below: "below",
    detail: (symbol, price, relation, flip) => `${symbol} ${price} — ${relation} the Flip (${flip})`,
    updateFrequency: "Updates every 30 seconds",
  },
  derivedMetricsBar: {
    ariaLabel: "Derived metrics",
    volatilityWindowNote: "Window: 60 days",
    accumulating: (daysAccumulated) => `Accumulating data — ${daysAccumulated}d/20d`,
  },
  expectedMoveWidget: {
    ariaLabel: "Expected move",
    primary: (dollars, pct, lower, upper) =>
      `Expected move: ±${dollars} (${pct}%) — Range: ${lower} – ${upper}`,
    remaining: (dollars, pct) => `Remaining for the day: ±${dollars} (${pct}%)`,
  },
  priceChart: {
    eyebrow: "Intraday price · client-side memory",
    title: (symbol, timeframeLabel) => `${symbol} · ${timeframeLabel}`,
    timeframeLabels: {
      "1m": "1-minute candles",
      "5m": "5-minute candles",
      "15m": "15-minute candles",
      "1h": "1-hour candles",
    },
    levelModeAriaLabel: "Level mode",
    levelsLegend: "Levels:",
    staticButton: "Static",
    historicalButton: "Historical",
    overlaysAriaLabel: "Overlays",
    overlaysLegend: "Overlays:",
    vwapAnchoredLabel: "Anchored VWAP",
    atrRangeLabel: "ATR Range",
    drawingToolsAriaLabel: "Drawing tools",
    drawingToolsLegend: "Draw:",
    trendlineButton: "Trendline",
    clearTrendlineButton: "Clear line",
    chartAriaLabel: (symbol) => `Candlestick chart for ${symbol}`,
    emptyState: "Waiting for the first price sample…",
  },
  chartSecondaryPanel: {
    ariaLabel: "GEX by strike and accumulated Whale Alerts flow",
    eyebrow: "Convexa",
    toggleGroupAriaLabel: "Secondary panel view",
    gexToggleButton: "GEX by Strike",
    flowToggleButton: "Whale Alerts Flow",
    gexChartAriaLabel: (symbol) => `GEX by strike for ${symbol}`,
    gexSpotPriceAriaLabel: (price) => `Spot price: ${price}`,
    gexLoading: "Loading GEX by strike…",
    gexNoBreakdown: "No per-strike breakdown available.",
    flowChartAriaLabel: (symbol) => `Accumulated Whale Alerts flow for ${symbol}`,
    flowLoading: "Loading Whale Alerts flow…",
    flowEmpty: "No alerts yet this session.",
    flowSinceLabel: (time) => `Data since ${time} — backend memory only, not persisted`,
  },
  preSessionPanel: {
    eyebrow: "Pre-session preparation",
    title: (symbol) => `GEX Profile — ${symbol}`,
    frozenPill: (closeDate) => `Frozen from the close on ${closeDate}`,
    chartAriaLabel: (symbol, closeDate) => `Frozen GEX Profile for ${symbol}, close on ${closeDate}`,
    noBreakdown: "No per-strike breakdown available for this snapshot.",
    loading: "Loading the frozen snapshot from the previous close…",
    legendAriaLabel: "Legend",
  },
  closingDynamicsPanel: {
    ariaLabel: "Closing Dynamics",
    eyebrow: "Closing dynamics",
    pinRiskScoreLabel: "Pin Risk Score",
    magnetStrikeLabel: "Magnet strike",
    charmTimeDecayBuy: "Time decay is pushing dealers to buy",
    charmTimeDecaySell: "Time decay is pushing dealers to sell",
    charmNeutral: "Neutral — no directional pressure from time decay",
    vannaIvIncreaseBuy: "A rise in volatility would push dealers to buy",
    vannaIvIncreaseSell: "A rise in volatility would push dealers to sell",
    vannaNeutral: "Neutral — no directional pressure from volatility",
  },
  volatilitySmile: {
    eyebrow: "Options Chain · Raw IV by strike",
    roleSubtitle: "Premium risk sentiment · not a gravitational level",
    expirationLabel: "Expiration",
    chartAriaLabel: (symbol, expiration) => `Volatility Smile for ${symbol} on ${expiration}`,
    atmAriaLabel: (strike) => `ATM strike ${strike}`,
    pointAriaLabel: (type, strike, ivPct) => `${type} strike ${strike}, IV ${ivPct}%`,
    legendAriaLabel: "Legend",
    loading: "Loading expirations and implied volatility…",
  },
  alertsPanel: {
    eyebrow: "Whale Alerts",
    title: "Alerts",
    empty: "No recent alerts.",
    recentAriaLabel: "Recent alerts",
    sideTabsAriaLabel: "Side",
    bvcLabel: "Estimated buy/sell (BVC)",
    bvcAriaLabel: (buyPct, sellPct) =>
      `Estimated: ${buyPct}% buy, ${sellPct}% sell — not confirmed data`,
  },
  quickScreener: {
    eyebrow: "Convexa Presets",
    title: "Quick Screener",
    presetLabel: "Preset",
    noResults: "No persisted results for this preset.",
    loading: "Loading preset…",
    headers: {
      symbol: "Symbol",
      contract: "Contract",
      type: "Type",
      amount: "Amount",
      time: "Time",
      updated: "Updated",
      exposure: "Exposure",
    },
  },
  whaleThresholdsPanel: {
    title: "Whale Alerts Thresholds",
    description:
      "Per-symbol calibration — determines when each alert type fires. Changes apply immediately, no backend restart required.",
    closeButtonAriaLabel: "Close",
    loading: "Loading thresholds…",
    loadFailed: "Couldn't load the thresholds.",
    saveButton: "Save",
    savingButton: "Saving…",
    savedConfirmation: "Saved",
    saveFailed: "Couldn't save",
    validationError: "All 5 fields must be positive numbers",
    headers: {
      symbol: "Symbol",
      unusualMin: "Unusual min. ($)",
      whaleMin: "Whale min. ($)",
      unusualMultiplier: "Unusual multiplier",
      whaleMultiplier: "Whale multiplier",
      sustainedFlowMin: "Sustained Flow min. ($)",
      actions: "Actions",
    },
  },
  screenerPresetSettingsPanel: {
    title: "Preset Filter Settings",
    description:
      "Editable filter criteria for the presets that have one. Changes apply immediately, no backend restart required.",
    triggerAriaLabel: "Preset filter settings",
    closeButtonAriaLabel: "Close",
    loading: "Loading settings…",
    loadFailed: "Couldn't load the preset settings.",
    saveButton: "Save",
    savingButton: "Saving…",
    savedConfirmation: "Saved",
    saveFailed: "Couldn't save",
    gammaValidationError: "Net Gamma Max must be a number",
    exposureValidationError:
      "Min. magnitude must be a non-negative number (or empty) and limit a positive integer (or empty)",
    notApplicable: "—",
    presetLabel: {
      "negative-gamma-board": "Negative Gamma Board",
      "vanna-exposure-leaders": "Vanna Exposure Leaders",
      "charm-decay-pressure": "Charm Decay Pressure",
    },
    headers: {
      preset: "Preset",
      netGammaMax: "Net Gamma Max",
      minMagnitude: "Min. Magnitude",
      limit: "Limit (top-N)",
      actions: "Actions",
    },
  },
  enginesGuide: {
    eyebrow: "Convexa",
    title: "Engine Interpretation Guide",
    description: "What each Convexa engine calculates and how to read it — replaces the PDF guide.",
    triggerAriaLabel: "Engine interpretation guide",
    closeButtonAriaLabel: "Close",
    standardBadge: "🟢 Validated standard",
    proprietaryBadge: "🟡 Convexa proprietary metric",
    citationLabel: "Academic source",
    engines: {
      gammaExposure: {
        name: "Gamma Exposure (GEX) / Net Gamma",
        description:
          "Aggregate net gamma exposure of options dealers for an underlying — its sign determines the regime (Long/Short Gamma) shown on the Regime Badge. It's the base Gamma Flip, Call/Put Wall, Max Pain, and Absolute Gamma Strike are all derived from.",
      },
      gammaFlip: {
        name: "Gamma Flip",
        description:
          "The market's regime boundary: price above it means Long Gamma (dampens volatility), below it means Short Gamma (amplifies it). If price already crossed it since the last recalculation, the regime is marked \"price-based\", lower confidence.",
      },
      absoluteGammaStrike: {
        name: "Absolute Gamma Strike",
        description:
          "The Gravity Map's most literal price magnet — the strike with the highest absolute gamma. When it coincides with or sits very close to Gamma Flip, both merge into one visual marker instead of showing as two.",
      },
      callPutWalls: {
        name: "Call Wall / Put Wall",
        description:
          "The day's most likely range boundaries, marked at the edges of the Gravity Map — along with Gamma Flip and Absolute Gamma, they reflect dealers' mechanical hedging pressure on price, not market sentiment.",
      },
      maxPain: {
        name: "Max Pain",
        description:
          "The strike where the least total option value would expire — a closing-price theory. It gains weight as the session close approaches, and appears in Pre-Session Preparation and Closing Dynamics.",
      },
      vegaExposure: {
        name: "Vega Exposure",
        description:
          "Aggregate exposure to implied-volatility changes — same pattern as GEX (Vega × Open Interest × 100, per 1% change in IV).",
      },
      thetaExposure: {
        name: "Theta Exposure",
        description: "Aggregate exposure to the passage of time — same calculation as Vega Exposure, with Theta.",
      },
      vannaExposure: {
        name: "Vanna Exposure",
        description:
          "Sensitivity of dealers' delta to changes in implied volatility. A positive value means rising IV pushes dealers to buy; negative means it pushes them to sell.",
      },
      charmExposure: {
        name: "Charm Exposure",
        description:
          "Sensitivity of dealers' delta to the simple passage of time. A positive value means the passage of time pushes dealers to buy; negative means it pushes them to sell.",
      },
      whaleAlertsBvc: {
        name: "Whale Alerts (Bulk Volume Classification)",
        description:
          "A buy/sell estimate derived from price movement alone — never a confirmed measurement of the real side of a trade. Every alert (Whale/Unusual/Sustained Flow) includes its estimated buy/sell volume from this method.",
      },
      anchoredVwap: {
        name: "Anchored VWAP",
        description:
          "A level where price tends to react, anchored to the session open (9:30 ET) and calculated with standard industry math — not a proprietary formula from any vendor.",
      },
      atrRange: {
        name: "ATR Range (Expected Historical Range)",
        description:
          "An expected price band anchored to the day's open, using classic technical-analysis True Range/ATR. The construction is Convexa's own, but the underlying formula is public domain.",
      },
      expectedMove: {
        name: "Expected Move",
        description:
          "A statistical measure derived from implied volatility (±1 standard deviation) — complements, not replaces, the Walls, which are mechanical hedging pressure, not a statistical measure.",
      },
      volatilityRegime: {
        name: "Volatility Regime / IV Rank",
        description:
          "Percentile of today's IV against the underlying's own last 60 days (Low/Moderate/High). The concept itself is an industry standard — the 60-day window instead of the usual 52 weeks is Convexa's only adaptation.",
      },
      dealerImpactScore: {
        name: "Dealer Impact Score",
        description:
          "A percentile of how extreme today's Net Gamma is compared to the underlying's own last 60 days. Not a probability of a trade working out — it measures how unusual today's reading is against the asset's own history.",
      },
      signalAlignmentScore: {
        name: "Signal Alignment Score",
        description:
          "Measures how much the signals Convexa already calculates agree with each other (regime agreement, data freshness, regime extremity) — not how extreme the regime itself is, that's Dealer Impact Score's job.",
      },
      marketBias: {
        name: "Market Bias",
        description:
          "Directional bias of options positioning (Put/Call OI Ratio + IV skew) — distinct from the Regime Badge's volatility regime, which describes behavior, not direction.",
      },
      pinRiskScore: {
        name: "Pin Risk Score",
        description:
          "How likely price is to get \"pinned\" near a specific strike at the close, combining Open Interest concentration, proximity to the magnet strike, and remaining session time.",
      },
    },
  },
};
