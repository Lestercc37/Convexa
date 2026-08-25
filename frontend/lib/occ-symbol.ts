export type ContractSide = "call" | "put";

// OCC option symbol format: {root}{YYMMDD}{C|P}{8-digit strike} — e.g.
// "SPY260220C00540000". The side character sits at a fixed position, 9
// characters from the end (right before the 8-digit strike) — same
// position-based parsing already used by the TradingView script to
// identify Call/Put. `WhaleAlert` has no dedicated side field (backend
// entity, API response, and frontend type all confirmed to lack one) —
// this is the only place that information exists.
const SIDE_OFFSET_FROM_END = 9;

export function parseContractSide(occSymbol: string): ContractSide | null {
  const sideChar = occSymbol.charAt(occSymbol.length - SIDE_OFFSET_FROM_END);
  if (sideChar === "C") return "call";
  if (sideChar === "P") return "put";
  return null;
}
