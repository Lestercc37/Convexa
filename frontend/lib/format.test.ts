import { describe, expect, it } from "vitest";
import { formatCurrencyOrDash, formatNumberOrDash } from "./format";

describe("formatNumberOrDash", () => {
  it("renders an em dash for null", () => {
    expect(formatNumberOrDash(null)).toBe("—");
  });

  it("formats a real number", () => {
    expect(formatNumberOrDash(548.5)).toBe("548.5");
  });
});

describe("formatCurrencyOrDash", () => {
  it("renders an em dash for null", () => {
    expect(formatCurrencyOrDash(null)).toBe("—");
  });

  it("formats a real number as USD currency", () => {
    expect(formatCurrencyOrDash(1500)).toBe("$1,500");
  });
});
