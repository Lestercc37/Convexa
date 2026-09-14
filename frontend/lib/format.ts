// Shared null-value placeholder convention (coordinated decision,
// 2026-09-14): an em dash for a value that is genuinely absent (e.g.
// gamma_flip with no sign crossing found in range), not "0" or a blank
// string -- extracted from quick-screener.tsx, the first place this
// pattern was used, so every future textual consumer of a nullable
// gamma-derived number reuses the same convention instead of inventing
// its own.
const NUMBER_FORMAT = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const CURRENCY_FORMAT = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

export function formatNumberOrDash(value: number | null): string {
  return value === null ? "—" : NUMBER_FORMAT.format(value);
}

export function formatCurrencyOrDash(value: number | null): string {
  return value === null ? "—" : CURRENCY_FORMAT.format(value);
}
