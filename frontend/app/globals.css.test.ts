import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const cssPath = join(process.cwd(), "app", "globals.css");

describe(".atr-band stacking", () => {
  it("keeps a z-index above Lightweight Charts' own canvas layers (max observed: 2)", () => {
    const css = readFileSync(cssPath, "utf-8");
    const rule = css.match(/\.atr-band\s*\{[^}]*\}/)?.[0];
    expect(rule, ".atr-band rule not found in globals.css").toBeDefined();

    const zIndexMatch = rule?.match(/z-index:\s*(\d+)/);
    expect(zIndexMatch, ".atr-band has no z-index declared").not.toBeNull();

    // Lightweight Charts stacks its own internal canvas layers up to
    // z-index 2 (confirmed live via getComputedStyle on each canvas). If
    // .atr-band's z-index doesn't clear that, the chart's own canvases
    // paint over the ATR band overlay and it becomes invisible —
    // regardless of correct top/height positioning. See the comment
    // above .atr-band in globals.css for the full rationale.
    const zIndex = Number(zIndexMatch?.[1]);
    expect(zIndex).toBeGreaterThan(2);
  });
});

describe(".chart-empty stacking", () => {
  it("keeps a z-index above Lightweight Charts' own canvas layers, same as .atr-band (regression, 2026-09-24)", () => {
    // The chart's own canvas background switched from "transparent" to a
    // solid color (see PriceChart's own createChart() call) -- without a
    // z-index here, that opaque canvas paints over this message instead of
    // letting it show through, which is exactly what happened live before
    // market open with zero candles for the day: the chart looked
    // completely blank instead of showing "waiting for first price".
    const css = readFileSync(cssPath, "utf-8");
    const rule = css.match(/\.chart-empty\s*\{[^}]*\}/)?.[0];
    expect(rule, ".chart-empty rule not found in globals.css").toBeDefined();

    const zIndexMatch = rule?.match(/z-index:\s*(\d+)/);
    expect(zIndexMatch, ".chart-empty has no z-index declared").not.toBeNull();

    const zIndex = Number(zIndexMatch?.[1]);
    expect(zIndex).toBeGreaterThan(2);
  });
});
