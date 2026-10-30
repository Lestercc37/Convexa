// Pure helpers of the desktop shell (no Electron import, so `node --test` can check them).

const LAYERS = [
  { id: "heatmap-gamma", label: "Heatmap Gamma" },
  { id: "heatmap-abs", label: "Heatmap Abs" },
  { id: "heatmap-total", label: "Heatmap Total" },
  { id: "heatmap-delta", label: "Heatmap Delta" },
  { id: "heatmap-vanna", label: "Heatmap Vanna" },
  { id: "heatmap-charm", label: "Heatmap Charm" },
  { id: "heatmap-calls", label: "Heatmap Calls" },
  { id: "heatmap-puts", label: "Heatmap Puts" },
  { id: "profile", label: "Perfil GEX" },
  { id: "smile", label: "Smile" },
  { id: "flow", label: "Flujo por minuto" },
  { id: "cubes", label: "Cubos de alertas" },
];

// What a layer window copies from the main window: symbol, timeframe, range and expiration tab (never the layer itself).
const SHARED_KEYS = ["simbolo", "tf", "rango", "dte"];

function sharedQuery(url) {
  const params = new URL(url).searchParams;
  const shared = new URLSearchParams();
  for (const key of SHARED_KEYS) {
    const value = params.get(key);
    if (value) shared.set(key, value);
  }
  return shared;
}

/** The address of a layer window: the main window's symbol / timeframe / range / tab plus `capa`. */
function layerUrl(base, mainUrl, layer) {
  const query = mainUrl ? sharedQuery(mainUrl) : new URLSearchParams();
  query.set("capa", layer);
  return `${base}/vista/grafico?${query.toString()}`;
}

/** True when a layer window shows another symbol / timeframe / range / tab than the main window (it then reloads on the new one). */
function needsSync(mainUrl, layerWindowUrl) {
  if (!mainUrl || !layerWindowUrl) return false;
  const a = sharedQuery(mainUrl);
  const b = sharedQuery(layerWindowUrl);
  return SHARED_KEYS.some((key) => a.get(key) !== b.get(key));
}

/** Only our own site may load inside the app's windows. */
function isOwnOrigin(base, url) {
  try {
    return new URL(url).origin === new URL(base).origin;
  } catch {
    return false;
  }
}

/** A remembered window size and place, kept only if it still fits on one of the screens (a monitor may be gone). */
function usableBounds(saved, displays) {
  if (!saved || typeof saved.width !== "number" || typeof saved.height !== "number") return null;
  const fits = displays.some(
    (area) =>
      saved.x >= area.x - 50 &&
      saved.y >= area.y - 50 &&
      saved.x + Math.min(saved.width, 200) <= area.x + area.width + 50 &&
      saved.y + 100 <= area.y + area.height + 50,
  );
  return fits ? saved : { width: saved.width, height: saved.height };
}

module.exports = { LAYERS, layerUrl, needsSync, isOwnOrigin, usableBounds };
