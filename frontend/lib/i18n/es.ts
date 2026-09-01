import type { Translations } from "./translations";

export const es: Translations = {
  common: {
    languageSwitcherAriaLabel: "Idioma",
    convexaNote: "métrica propia de Convexa, no un estándar de mercado",
  },
  errors: {
    notFound: "No se encontró el recurso solicitado.",
    requestFailed: "No se pudo completar la solicitud. Intenta de nuevo.",
  },
  dashboard: {
    underlyingLabel: "Subyacente",
    timeframeGroupAriaLabel: "Marco temporal",
    viewGroupAriaLabel: "Vista",
    liveButton: "En vivo",
    preSessionButton: "Pre-Sesión",
    exposureGroupAriaLabel: "Charm y Vanna Exposure",
    aggregatedGreeksEyebrow: "Griegas agregadas",
    loadingRegime: "Cargando régimen y niveles…",
    settingsButtonAriaLabel: "Configuración de umbrales de Whale Alerts",
    resizeSeparatorAriaLabel: "Redimensionar paneles",
  },
  regimeBadge: {
    ariaLabel: "Régimen gamma",
    currentRegimeEyebrow: "Régimen actual",
    transientAriaLabel: "Régimen transitorio",
    unconfirmedTooltip:
      "El precio cruzó el Gamma Flip antes del último recálculo del agregado — régimen basado en precio.",
    above: "arriba",
    below: "debajo",
    detail: (symbol, price, relation, flip) =>
      `${symbol} ${price} — ${relation} del Flip (${flip})`,
    updateFrequency: "Actualización cada 30 segundos",
  },
  derivedMetricsBar: {
    ariaLabel: "Métricas derivadas",
    volatilityWindowNote: "Ventana: 60 días",
    accumulating: (daysAccumulated) => `Acumulando datos — ${daysAccumulated}d/20d`,
  },
  expectedMoveWidget: {
    ariaLabel: "Movimiento esperado",
    primary: (dollars, pct, lower, upper) =>
      `Movimiento esperado: ±${dollars} (${pct}%) — Rango: ${lower} – ${upper}`,
    remaining: (dollars, pct) => `Remanente del día: ±${dollars} (${pct}%)`,
  },
  priceChart: {
    eyebrow: "Precio intradía · memoria local",
    title: (symbol, timeframeLabel) => `${symbol} · ${timeframeLabel}`,
    timeframeLabels: {
      "1m": "Velas de 1 minuto",
      "5m": "Velas de 5 minutos",
      "15m": "Velas de 15 minutos",
      "1h": "Velas de 1 hora",
    },
    levelModeAriaLabel: "Modo de niveles",
    levelsLegend: "Niveles:",
    staticButton: "Estático",
    historicalButton: "Histórico",
    overlaysAriaLabel: "Overlays",
    overlaysLegend: "Overlays:",
    vwapAnchoredLabel: "VWAP Anclado",
    atrRangeLabel: "Rango ATR",
    drawingToolsAriaLabel: "Herramientas de dibujo",
    drawingToolsLegend: "Dibujo:",
    trendlineButton: "Línea de tendencia",
    clearTrendlineButton: "Borrar línea",
    chartAriaLabel: (symbol) => `Chart de velas para ${symbol}`,
    emptyState: "Esperando la primera muestra de precio…",
  },
  chartSecondaryPanel: {
    ariaLabel: "GEX por strike y flujo acumulado de Whale Alerts",
    eyebrow: "Convexa",
    toggleGroupAriaLabel: "Vista del panel secundario",
    gexToggleButton: "GEX por Strike",
    flowToggleButton: "Flujo Whale Alerts",
    gexChartAriaLabel: (symbol) => `GEX por strike para ${symbol}`,
    gexSpotPriceAriaLabel: (price) => `Precio spot: ${price}`,
    gexLoading: "Cargando GEX por strike…",
    gexNoBreakdown: "Sin desglose por strike disponible.",
    flowChartAriaLabel: (symbol) => `Flujo acumulado de Whale Alerts para ${symbol}`,
    flowLoading: "Cargando flujo de Whale Alerts…",
    flowEmpty: "Sin alertas todavía en esta sesión.",
    flowSinceLabel: (time) =>
      `Datos desde las ${time} — memoria del backend, sin persistencia`,
  },
  preSessionPanel: {
    eyebrow: "Preparación pre-sesión",
    title: (symbol) => `GEX Profile — ${symbol}`,
    frozenPill: (closeDate) => `Congelado desde el cierre de ${closeDate}`,
    chartAriaLabel: (symbol, closeDate) =>
      `GEX Profile congelado de ${symbol}, cierre del ${closeDate}`,
    noBreakdown: "Sin desglose por strike disponible para este snapshot.",
    loading: "Cargando snapshot congelado del cierre anterior…",
    legendAriaLabel: "Leyenda",
  },
  closingDynamicsPanel: {
    ariaLabel: "Dinámica de Cierre",
    eyebrow: "Dinámica de cierre",
    pinRiskScoreLabel: "Pin Risk Score",
    magnetStrikeLabel: "Strike imán",
    charmTimeDecayBuy: "El paso del tiempo empuja a los dealers a comprar",
    charmTimeDecaySell: "El paso del tiempo empuja a los dealers a vender",
    charmNeutral: "Neutral — sin presión direccional por paso del tiempo",
    vannaIvIncreaseBuy: "Un aumento de volatilidad empujaría a los dealers a comprar",
    vannaIvIncreaseSell: "Un aumento de volatilidad empujaría a los dealers a vender",
    vannaNeutral: "Neutral — sin presión direccional por volatilidad",
  },
  volatilitySmile: {
    eyebrow: "Options Chain · IV cruda por strike",
    roleSubtitle: "Sentimiento de riesgo en prima · no es un nivel de gravitación",
    expirationLabel: "Vencimiento",
    chartAriaLabel: (symbol, expiration) => `Volatility Smile de ${symbol} para ${expiration}`,
    atmAriaLabel: (strike) => `Strike ATM ${strike}`,
    pointAriaLabel: (type, strike, ivPct) => `${type} strike ${strike}, IV ${ivPct}%`,
    legendAriaLabel: "Leyenda",
    loading: "Cargando vencimientos y volatilidad implícita…",
  },
  alertsPanel: {
    eyebrow: "Whale Alerts",
    title: "Alertas",
    empty: "Sin alertas recientes.",
    recentAriaLabel: "Alertas recientes",
    sideTabsAriaLabel: "Bando",
    bvcLabel: "Compra/venta estimado (BVC)",
    bvcAriaLabel: (buyPct, sellPct) =>
      `Estimado: ${buyPct}% compra, ${sellPct}% venta — no es dato confirmado`,
  },
  quickScreener: {
    eyebrow: "Presets Convexa",
    title: "Escáner Rápido",
    presetLabel: "Preset",
    noResults: "No hay resultados persistidos para este preset.",
    loading: "Cargando preset…",
    headers: {
      symbol: "Símbolo",
      contract: "Contrato",
      type: "Tipo",
      amount: "Monto",
      time: "Hora",
      updated: "Actualizado",
      exposure: "Exposición",
    },
  },
  whaleThresholdsPanel: {
    title: "Umbrales de Whale Alerts",
    description:
      "Calibración por símbolo — determina cuándo se dispara cada tipo de alerta. Los cambios aplican de inmediato, sin reiniciar el backend.",
    closeButtonAriaLabel: "Cerrar",
    loading: "Cargando umbrales…",
    loadFailed: "No se pudieron cargar los umbrales.",
    saveButton: "Guardar",
    savingButton: "Guardando…",
    savedConfirmation: "Guardado",
    saveFailed: "No se pudo guardar",
    validationError: "Los 5 campos deben ser números positivos",
    headers: {
      symbol: "Símbolo",
      unusualMin: "Unusual mín. ($)",
      whaleMin: "Whale mín. ($)",
      unusualMultiplier: "Multiplicador Unusual",
      whaleMultiplier: "Multiplicador Whale",
      sustainedFlowMin: "Flujo Sostenido mín. ($)",
      actions: "Acciones",
    },
  },
  screenerPresetSettingsPanel: {
    title: "Configuración de Filtros de Presets",
    description:
      "Criterios de filtro editables para los presets que tienen uno. Los cambios aplican de inmediato, sin reiniciar el backend.",
    triggerAriaLabel: "Configuración de filtros de presets",
    closeButtonAriaLabel: "Cerrar",
    loading: "Cargando configuración…",
    loadFailed: "No se pudo cargar la configuración de presets.",
    saveButton: "Guardar",
    savingButton: "Guardando…",
    savedConfirmation: "Guardado",
    saveFailed: "No se pudo guardar",
    gammaValidationError: "Net Gamma Max debe ser un número",
    exposureValidationError:
      "Magnitud mín. debe ser un número no negativo (o vacío) y el límite un entero positivo (o vacío)",
    notApplicable: "—",
    presetLabel: {
      "negative-gamma-board": "Negative Gamma Board",
      "vanna-exposure-leaders": "Vanna Exposure Leaders",
      "charm-decay-pressure": "Charm Decay Pressure",
    },
    headers: {
      preset: "Preset",
      netGammaMax: "Net Gamma Max",
      minMagnitude: "Magnitud Mín.",
      limit: "Límite (top-N)",
      actions: "Acciones",
    },
  },
  enginesGuide: {
    eyebrow: "Convexa",
    title: "Guía de Interpretación de los Motores",
    description:
      "Qué calcula cada motor de Convexa y cómo interpretarlo — reemplaza la guía en PDF.",
    triggerAriaLabel: "Guía de interpretación de los motores",
    closeButtonAriaLabel: "Cerrar",
    standardBadge: "🟢 Estándar validado",
    proprietaryBadge: "🟡 Métrica propia de Convexa",
    citationLabel: "Fuente académica",
    engines: {
      gammaExposure: {
        name: "Gamma Exposure (GEX) / Net Gamma",
        description:
          "Exposición gamma neta agregada de los dealers de opciones para un underlying — su signo determina el régimen (Long/Short Gamma) del Regime Badge. Es la base de la que se derivan Gamma Flip, Call/Put Wall, Max Pain y Absolute Gamma Strike.",
      },
      gammaFlip: {
        name: "Gamma Flip",
        description:
          "El límite de régimen del mercado: precio por encima indica Long Gamma (amortigua volatilidad), por debajo indica Short Gamma (la amplifica). Si el precio ya lo cruzó antes del último recálculo, el régimen se marca como \"basado en precio\", de menor confianza.",
      },
      absoluteGammaStrike: {
        name: "Absolute Gamma Strike",
        description:
          "El imán de precio más literal del Mapa de Gravitación — el strike con mayor gamma absoluta. Cuando coincide o está muy cerca del Gamma Flip, se fusionan visualmente en vez de mostrarse como dos marcas separadas.",
      },
      callPutWalls: {
        name: "Call Wall / Put Wall",
        description:
          "Los límites de rango más probables del día, marcados en los extremos del Mapa de Gravitación — junto con Gamma Flip y Absolute Gamma, reflejan presión mecánica de hedging de dealers sobre el precio, no sentimiento de mercado.",
      },
      maxPain: {
        name: "Max Pain",
        description:
          "El strike donde expiraría la menor cantidad de valor total de opciones — teoría de precio de cierre. Gana peso conforme se acerca el cierre de sesión, y aparece en Preparación Pre-Sesión y Dinámica de Cierre.",
      },
      vegaExposure: {
        name: "Vega Exposure",
        description:
          "Exposición agregada a cambios de volatilidad implícita — mismo patrón que GEX (Vega × Open Interest × 100, por cada 1% de cambio en IV).",
      },
      thetaExposure: {
        name: "Theta Exposure",
        description: "Exposición agregada al paso del tiempo — mismo cálculo que Vega Exposure, con Theta.",
      },
      vannaExposure: {
        name: "Vanna Exposure",
        description:
          "Sensibilidad del delta de los dealers ante cambios de volatilidad implícita. Un valor positivo indica que un aumento de IV empuja a los dealers a comprar; negativo, a vender.",
      },
      charmExposure: {
        name: "Charm Exposure",
        description:
          "Sensibilidad del delta de los dealers al simple paso del tiempo. Un valor positivo indica que el paso del tiempo empuja a los dealers a comprar; negativo, a vender.",
      },
      whaleAlertsBvc: {
        name: "Whale Alerts (Bulk Volume Classification)",
        description:
          "Estimación de compra/venta a partir del movimiento de precio — nunca una medición confirmada del lado real de una operación. Cada alerta (Whale/Unusual/Sustained Flow) incluye su volumen de compra/venta estimado con este método.",
      },
      anchoredVwap: {
        name: "Anchored VWAP",
        description:
          "Nivel donde el precio suele reaccionar, anclado a la apertura de sesión (9:30 ET) y calculado con matemática estándar de industria — no es una fórmula propietaria de ningún proveedor.",
      },
      atrRange: {
        name: "Rango ATR (Histórico Esperado)",
        description:
          "Banda de precio esperado anclada a la apertura del día, con True Range/ATR clásico de análisis técnico. La construcción es propia de Convexa, pero la fórmula subyacente es de dominio público.",
      },
      expectedMove: {
        name: "Movimiento Esperado",
        description:
          "Medida estadística derivada de la volatilidad implícita (±1 desviación estándar) — complementa, no sustituye, a los Walls, que son presión mecánica de hedging, no una medida estadística.",
      },
      volatilityRegime: {
        name: "Volatility Regime / IV Rank",
        description:
          "Percentil de la IV actual contra los últimos 60 días del propio underlying (Low/Moderate/High). El concepto es estándar de industria — la ventana de 60 días en vez de las 52 semanas habituales es la única adaptación propia de Convexa.",
      },
      dealerImpactScore: {
        name: "Dealer Impact Score",
        description:
          "Percentil de qué tan extremo es el Net Gamma de hoy comparado con los últimos 60 días del propio underlying. No es una probabilidad de acierto — mide qué tan inusual es la lectura de hoy respecto a la propia historia del activo.",
      },
      signalAlignmentScore: {
        name: "Signal Alignment Score",
        description:
          "Mide qué tan de acuerdo están las señales que Convexa ya calcula (acuerdo de régimen, frescura del dato, extremidad del régimen) — no qué tan extremo es el régimen en sí, eso lo hace Dealer Impact Score.",
      },
      marketBias: {
        name: "Market Bias",
        description:
          "Sesgo direccional del posicionamiento de opciones (Put/Call OI Ratio + skew de IV) — distinto del régimen de volatilidad del Regime Badge, que describe comportamiento, no dirección.",
      },
      pinRiskScore: {
        name: "Pin Risk Score",
        description:
          "Qué tan probable es que el precio quede \"clavado\" cerca de un strike específico al cierre, combinando concentración de Open Interest, cercanía al strike imán y tiempo restante de sesión.",
      },
    },
  },
};
