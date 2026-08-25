import { describe, expect, it } from "vitest";
import { parseContractSide } from "./occ-symbol";

describe("parseContractSide", () => {
  it("parses a known real call OCC symbol", () => {
    // Hand-verified: "SPY" + "260220" (2026-02-20) + "C" + "00540000"
    // (strike 540.000 * 1000, 8 digits) — same symbol already confirmed
    // in backend/adapters/providers/mock/provider.py's construction.
    expect(parseContractSide("SPY260220C00540000")).toBe("call");
  });

  it("parses a known real put OCC symbol", () => {
    expect(parseContractSide("SPY260220P00540000")).toBe("put");
  });

  it("parses correctly regardless of root symbol length", () => {
    expect(parseContractSide("GOOGL260220C00150000")).toBe("call");
    expect(parseContractSide("QQQ260220P00480000")).toBe("put");
  });

  it("returns null for a malformed or unrecognized symbol", () => {
    expect(parseContractSide("not-an-occ-symbol")).toBeNull();
    expect(parseContractSide("")).toBeNull();
  });
});
