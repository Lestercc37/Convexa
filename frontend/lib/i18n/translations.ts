export type Translations = {
  common: {
    languageSwitcherAriaLabel: string;
    convexaNote: string;
  };
  errors: {
    notFound: string;
    requestFailed: string;
  };
  dashboard: {
    underlyingLabel: string;
    timeframeGroupAriaLabel: string;
    viewGroupAriaLabel: string;
    liveButton: string;
    preSessionButton: string;
    exposureGroupAriaLabel: string;
    aggregatedGreeksEyebrow: string;
    loadingRegime: string;
    settingsButtonAriaLabel: string;
  };
  regimeBadge: {
    ariaLabel: string;
    currentRegimeEyebrow: string;
    transientAriaLabel: string;
    unconfirmedTooltip: string;
    above: string;
    below: string;
    detail: (symbol: string, price: string, relation: string, flip: string) => string;
    updateFrequency: string;
  };
  derivedMetricsBar: {
    ariaLabel: string;
    volatilityWindowNote: string;
    accumulating: (daysAccumulated: number) => string;
  };
  expectedMoveWidget: {
    ariaLabel: string;
    primary: (dollars: string, pct: string, lower: string, upper: string) => string;
    remaining: (dollars: string, pct: string) => string;
  };
  priceChart: {
    eyebrow: string;
    title: (symbol: string) => string;
    levelModeAriaLabel: string;
    levelsLegend: string;
    staticButton: string;
    historicalButton: string;
    overlaysAriaLabel: string;
    overlaysLegend: string;
    vwapAnchoredLabel: string;
    atrRangeLabel: string;
    chartAriaLabel: (symbol: string) => string;
    emptyState: string;
  };
  preSessionPanel: {
    eyebrow: string;
    title: (symbol: string) => string;
    frozenPill: (closeDate: string) => string;
    chartAriaLabel: (symbol: string, closeDate: string) => string;
    noBreakdown: string;
    loading: string;
    legendAriaLabel: string;
  };
  closingDynamicsPanel: {
    ariaLabel: string;
    eyebrow: string;
    pinRiskScoreLabel: string;
    magnetStrikeLabel: string;
    charmTimeDecayBuy: string;
    charmTimeDecaySell: string;
    charmNeutral: string;
    vannaIvIncreaseBuy: string;
    vannaIvIncreaseSell: string;
    vannaNeutral: string;
  };
  volatilitySmile: {
    eyebrow: string;
    roleSubtitle: string;
    expirationLabel: string;
    chartAriaLabel: (symbol: string, expiration: string) => string;
    atmAriaLabel: (strike: number) => string;
    pointAriaLabel: (type: string, strike: number, ivPct: string) => string;
    legendAriaLabel: string;
    loading: string;
  };
  alertsPanel: {
    eyebrow: string;
    title: string;
    empty: string;
    recentAriaLabel: string;
  };
  quickScreener: {
    eyebrow: string;
    title: string;
    presetLabel: string;
    noResults: string;
    loading: string;
    headers: {
      symbol: string;
      contract: string;
      type: string;
      amount: string;
      time: string;
      updated: string;
      exposure: string;
    };
  };
  whaleThresholdsPanel: {
    title: string;
    description: string;
    closeButtonAriaLabel: string;
    loading: string;
    loadFailed: string;
    saveButton: string;
    savingButton: string;
    savedConfirmation: string;
    saveFailed: string;
    validationError: string;
    headers: {
      symbol: string;
      unusualMin: string;
      whaleMin: string;
      unusualMultiplier: string;
      whaleMultiplier: string;
      sustainedFlowMin: string;
      actions: string;
    };
  };
};
