// Data behind the in-app engines reference panel (EnginesGuidePanel) —
// replaces the external PDF "Convexa — Guía de Interpretación Objetiva
// de los Motores". Classification and citations are language-independent
// (kept here); each entry's display name/description live in
// t.enginesGuide.engines[id] (frontend/lib/i18n) so they can be
// translated — this file only holds the stable ordering/id/metadata.
//
// Classification source: docs/derived-metrics.md §1-6 and §5's summary
// table draw an explicit line between engines that ARE industry
// standards (GEX, Gamma Flip, Vega/Theta Exposure, IV Rank, etc. — even
// when the specific window/implementation is Convexa's own) and the
// composite/proprietary ones (Dealer Impact Score, Signal Alignment
// Score, Market Bias) that always carry the "métrica propia de Convexa,
// no un estándar de mercado" note. Closing Dynamics/Pin Risk Score has
// no explicit 🟢/🟡 label in the docs, but dashboard-spec.md section 9
// says it should carry the same "no es estándar de mercado" note as
// those three — classified "proprietary" on that basis.

export type EngineClassification = "standard" | "proprietary";

export type EngineReferenceEntry = {
  id: string;
  classification: EngineClassification;
  citation?: string;
};

export const ENGINES_REFERENCE: EngineReferenceEntry[] = [
  { id: "gammaExposure", classification: "standard" },
  { id: "gammaFlip", classification: "standard" },
  { id: "absoluteGammaStrike", classification: "standard" },
  { id: "callPutWalls", classification: "standard" },
  { id: "maxPain", classification: "standard" },
  { id: "vegaExposure", classification: "standard" },
  { id: "thetaExposure", classification: "standard" },
  {
    id: "vannaExposure",
    classification: "standard",
    citation: "Black & Scholes (1973); Merton (1973); Haug (2007)",
  },
  {
    id: "charmExposure",
    classification: "standard",
    citation: "Black & Scholes (1973); Merton (1973); Haug (2007)",
  },
  {
    id: "whaleAlertsBvc",
    classification: "standard",
    citation: "Easley, López de Prado & O'Hara (2012)",
  },
  { id: "anchoredVwap", classification: "standard" },
  { id: "atrRange", classification: "standard" },
  { id: "expectedMove", classification: "standard" },
  { id: "volatilityRegime", classification: "standard" },
  { id: "dealerImpactScore", classification: "proprietary" },
  { id: "signalAlignmentScore", classification: "proprietary" },
  { id: "marketBias", classification: "proprietary" },
  { id: "pinRiskScore", classification: "proprietary" },
];
