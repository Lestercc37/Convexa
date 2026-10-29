const test = require("node:test");
const assert = require("node:assert");
const { LAYERS, layerUrl, needsSync, isOwnOrigin, usableBounds } = require("./windows");

const BASE = "https://dashboard.convexatrading.com";

test("a layer window copies the main window's symbol / timeframe / range / tab, never its layer", () => {
  const main = `${BASE}/vista/grafico?simbolo=NDX&tf=5m&rango=dia&dte=0dte`;
  assert.equal(layerUrl(BASE, main, "heatmap-delta"), `${BASE}/vista/grafico?simbolo=NDX&tf=5m&rango=dia&dte=0dte&capa=heatmap-delta`);
  assert.equal(layerUrl(BASE, null, "profile"), `${BASE}/vista/grafico?capa=profile`);
});

test("a layer window reloads only when the shared fields differ", () => {
  const main = `${BASE}/vista/grafico?simbolo=SPX&tf=1m`;
  assert.equal(needsSync(main, `${BASE}/vista/grafico?simbolo=SPX&tf=1m&capa=flow`), false);
  assert.equal(needsSync(main, `${BASE}/vista/grafico?simbolo=NDX&tf=1m&capa=flow`), true);
  assert.equal(needsSync(main, `${BASE}/vista/grafico?simbolo=SPX&tf=5m&capa=flow`), true);
});

test("only our own origin loads inside the app", () => {
  assert.equal(isOwnOrigin(BASE, `${BASE}/login`), true);
  assert.equal(isOwnOrigin(BASE, "https://dashboard.convexatrading.com.evil.com/"), false);
  assert.equal(isOwnOrigin(BASE, "not a url"), false);
});

test("a remembered place on a monitor that is gone keeps only the size", () => {
  const screens = [{ x: 0, y: 0, width: 1920, height: 1040 }];
  assert.deepEqual(usableBounds({ x: 100, y: 80, width: 1200, height: 800 }, screens), { x: 100, y: 80, width: 1200, height: 800 });
  assert.deepEqual(usableBounds({ x: 2500, y: 80, width: 1200, height: 800 }, screens), { width: 1200, height: 800 });
  assert.equal(usableBounds(null, screens), null);
});

test("every layer of the route has a menu entry", () => {
  assert.equal(LAYERS.length, 12);
  assert.equal(new Set(LAYERS.map((layer) => layer.id)).size, 12);
});
