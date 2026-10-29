"""Builds the flow chain prototype (2026-10-25) from flow_chain_export_1023.json (docs/hm_flow_chain_export_1023.py, read-only on the server):
docs/prototipo_cadena_flujo_SPX_2026-10-23.html, in Lester's own gex-flow.html style (his <style> block and fonts reused), with REAL Friday data.
Also prints the alert calibration: a strike alerts when its net delta flow over the last 5 minutes crosses |T| (one alert per crossing).
Usage: python docs/sim_flow_chain_build.py <export.json> <his gex-flow.html> [SYMBOL ROOT]
(2026-10-29: any symbol; the strikes come from the stored GEX items near the day's spot range, the 5M threshold is the one closest to ~35 alerts a day.)
"""
import json
import re
import statistics
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
his = open(sys.argv[2], encoding="utf-8").read()
style = re.search(r"<style>.*?</style>", his, re.S).group(0)
fonts = re.search(r'<link rel="stylesheet" href="https://fonts.googleapis.com[^>]+>', his).group(0)

SYMBOL = sys.argv[3] if len(sys.argv) > 3 else "SPX"
ROOT = sys.argv[4] if len(sys.argv) > 4 else "SPXW"
DAY = data.get("day", "2026-10-23")
if SYMBOL == "SPX":
    strikes = list(range(7450, 7851, 5))
else:
    known_spot = [x for x in data["spot"] if x]
    lo, hi = min(known_spot) * 0.99, max(known_spot) * 1.01
    present = sorted({float(k) for g in data["gex"] if g for k in g} | {float(k) for f in data["flow"] for k in f})
    strikes = [int(k) if k == int(k) else k for k in present if lo <= k <= hi]
N = 390
spot = [data["spot"][m] for m in range(N)]
for m in range(N):  # carry the last known value over gaps
    if spot[m] is None and m:
        spot[m] = spot[m - 1]
levels, gex = [], []
for m in range(N):
    lv = data["levels"][m] or (levels[-1] if levels else {})
    levels.append({"cw": lv.get("call_wall"), "pw": lv.get("put_wall"), "flip": lv.get("gamma_flip"), "abs": lv.get("absolute_gamma_strike")})
    g = data["gex"][m]
    gex.append([round(g[str(float(k))]) if g and str(float(k)) in g else None for k in strikes] if g else (gex[-1] if gex else [None] * len(strikes)))
calls = [[round(data["flow"][m].get(str(float(k)), [0] * 7)[0]) for k in strikes] for m in range(N)]
puts = [[round(data["flow"][m].get(str(float(k)), [0] * 7)[1]) for k in strikes] for m in range(N)]
ml = [[data["flow"][m].get(str(float(k)), [0] * 7)[4] for k in strikes] for m in range(N)]
signed = [sum(v[6] for v in data["flow"][m].values()) for m in range(N)]
mlt = [sum(v[4] for v in data["flow"][m].values()) for m in range(N)]
oth = [sum(v[5] for v in data["flow"][m].values()) for m in range(N)]

# ---- alert calibration: rolling 5-minute net flow per strike, an alert when |sum| crosses T upward
net = [[calls[m][i] + puts[m][i] for i in range(len(strikes))] for m in range(N)]
roll = [[sum(net[x][i] for x in range(max(0, m - 4), m + 1)) for i in range(len(strikes))] for m in range(N)]
print("strike 5-min |net| percentiles (cells with flow):", {p: round(statistics.quantiles([abs(v) for row in roll for v in row if v], n=1000)[int(p * 10) - 1]) for p in (90, 99, 99.5, 99.9)})
magnitudes = sorted(abs(v) for row in roll for v in row if v)
candidates = sorted({max(1, round(magnitudes[int(len(magnitudes) * q)] / 5) * 5) for q in (0.9, 0.95, 0.98, 0.99, 0.995, 0.998, 0.999)} | ({100, 150, 200, 250, 300} if SYMBOL == "SPX" else set()))
counts = {}
for T in candidates:
    alerts = []
    for i, k in enumerate(strikes):
        for m in range(1, N):
            if abs(roll[m][i]) >= T > abs(roll[m - 1][i]):
                alerts.append((m, k, roll[m][i]))
    dist = [abs(k - spot[m]) for m, k, _ in alerts]
    by_hour = {}
    for m, _, _ in alerts:
        by_hour[9 + (30 + m) // 60] = by_hour.get(9 + (30 + m) // 60, 0) + 1
    counts[T] = len(alerts)
    print(f"T={T:>5} delta contracts: {len(alerts):>3} alerts/day | bullish {sum(1 for a in alerts if a[2] > 0)} bearish {sum(1 for a in alerts if a[2] < 0)} | "
          f"median distance to spot {statistics.median(dist) if dist else 0:.0f} pts | per hour {dict(sorted(by_hour.items()))}")
print(f"day totals: signed contracts {sum(signed):,} | multi-leg {sum(mlt):,} | other {sum(oth):,} | signed share {100 * sum(signed) / (sum(signed) + sum(mlt) + sum(oth)):.0f} %")

THRESHOLD = 200 if SYMBOL == "SPX" else min(counts, key=lambda T: abs(counts[T] - 35))
print("threshold used", THRESHOLD, "->", counts.get(THRESHOLD), "alerts")
payload = json.dumps({"strikes": strikes, "spot": spot, "levels": levels, "gex": gex, "calls": calls, "puts": puts, "ml": ml,
                      "signed": signed, "mlt": mlt, "oth": oth}, separators=(",", ":"))

body = r"""
<div class="wrap">
  <div class="sim-banner"><span class="sim-dot"></span><span><b>Prototipo con datos reales del __DAY__ (__SYMBOL__).</b> Flujo = operaciones simples de 0DTE (__ROOT__) firmadas contra su bid/ask, en contratos-delta; las de paquetes multi-leg van aparte. GEX y niveles = los de Convexa (vista táctica 0-2 DTE). Mueve el Replay o pulsa ▶ para ver el día.</span></div>
  <div class="head">
    <div class="head-left">
      <div class="magnet-badge">🧲</div>
      <div class="head-title">
        <div class="eyebrow"><span>__SYMBOL__</span> · Vencimiento hoy (0DTE) · Cadena de flujo <span class="live-pill"><span class="dot"></span><span id="clock">—</span></span></div>
        <div class="iman-value"><span>Imán 15M:</span><span class="num mono" id="imanValue">—</span></div>
        <div class="levels-row"><span>PW <b id="pwVal">—</b></span><span>Flip <b id="zgVal">—</b></span><span>CW <b id="cwVal">—</b></span><span>ABS <b id="absVal">—</b></span></div>
      </div>
    </div>
    <div class="head-right">
      <div class="stat-group"><h4>Flujo del día</h4><div class="stat-row"><span>Neto</span><span class="mono" id="dayNet">—</span></div><div class="stat-row"><span>Con lado</span><span class="mono" id="dayShare">—</span></div></div>
      <div class="stat-group"><h4>Alertas</h4><div class="stat-row"><span>Hoy</span><span class="mono" id="alertCount">—</span></div><div class="stat-row"><span>Umbral 5M</span><span class="mono"><input type="text" id="thr" value="__THR__" style="width:70px"></span></div></div>
      <div class="stat-group"><h4>Mercado</h4><div class="stat-row"><span>Spot</span><span class="mono" id="spotVal">—</span></div><div class="stat-row"><span>Hora</span><span class="mono" id="tsLabel">—</span></div></div>
    </div>
  </div>
  <div class="controls">
    <div class="ctl-group"><span class="ctl-label">Replay del día</span>
      <div class="replay-slider"><button class="btn primary" id="play">▶</button><input type="range" id="slider" min="4" max="389" value="389" style="width:420px"><span class="mono" id="replayLabel" style="font-size:11px;color:var(--muted)"></span></div>
    </div>
  </div>
  <div class="legend">
    <span><span class="ic" style="font-size:14px">🧲</span><b>Imán</b> = el strike que más flujo neto recibió en esa ventana.</span>
    <span>Verde = presión alcista (compra de calls o venta de puts) · rojo = bajista.</span>
    <span><span class="tag zg" style="padding:1px 6px">Paquetes</span> = contratos en operaciones multi-leg (sin lado: se muestran aparte).</span>
    <span>Fila con borde = alerta: |flujo 5M| ≥ umbral.</span>
  </div>
  <div class="grid-scroll"><table class="mono">
    <thead><tr><th rowspan="2" style="min-width:68px">Strike</th><th rowspan="2" style="min-width:110px">GEX actual</th><th class="delta-head">Flujo 5M</th><th class="delta-head">Flujo 15M</th><th class="delta-head">Flujo 30M</th><th class="delta-head">Flujo 60M</th><th rowspan="2" style="min-width:90px">Paquetes 15M</th><th rowspan="2" style="min-width:110px">Nivel</th></tr>
    <tr class="sub"><th><div class="win-meta" id="m5"></div></th><th><div class="win-meta" id="m15"></div></th><th><div class="win-meta" id="m30"></div></th><th><div class="win-meta" id="m60"></div></th></tr></thead>
    <tbody id="tbody"></tbody></table></div>
  <div class="grid-scroll" style="padding:10px 14px"><b style="font-size:12px">Alertas hasta este minuto</b><div id="alertList" class="mono" style="font-size:12px;color:var(--muted);margin-top:6px"></div></div>
  <footer class="note">Cadena de flujo · prototipo de diseño con datos guardados — contexto, no pronóstico.</footer>
</div>
<style>
tbody tr.alert-row td{ box-shadow: inset 0 0 0 1px var(--warn); }
tbody tr.alert-row td:first-child{ animation: pulse-row 1.4s infinite; }
@keyframes pulse-row{ 0%{background:color-mix(in srgb, var(--warn) 25%, transparent)} 100%{background:transparent} }
</style>
<script>
const D = __DATA__;
const S = D.strikes, N = D.spot.length;
const cum = []; // cumulative net per strike
for (let m = 0; m < N; m++) cum.push(S.map((_, i) => (m ? cum[m - 1][i] : 0) + D.calls[m][i] + D.puts[m][i]));
const mlCum = []; for (let m = 0; m < N; m++) mlCum.push(S.map((_, i) => (m ? mlCum[m - 1][i] : 0) + D.ml[m][i]));
const cumC = [], cumP = [];
for (let m = 0; m < N; m++) { cumC.push(S.map((_, i) => (m ? cumC[m - 1][i] : 0) + D.calls[m][i])); cumP.push(S.map((_, i) => (m ? cumP[m - 1][i] : 0) + D.puts[m][i])); }
const win = (arr, m, w) => S.map((_, i) => arr[m][i] - (m - w >= 0 ? arr[m - w][i] : 0));
const fmt = v => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(Math.round(v)).toLocaleString("en-US");
const fmtM = v => v == null ? "—" : (v < 0 ? "-" : "+") + (Math.abs(v) >= 1e9 ? (Math.abs(v) / 1e9).toFixed(2) + "B" : (Math.abs(v) / 1e6).toFixed(1) + "M");
const hhmm = m => { const t = 9 * 60 + 30 + m; return String(Math.floor(t / 60)).padStart(2, "0") + ":" + String(t % 60).padStart(2, "0"); };
let minute = N - 1, timer = null;
function alertsUpTo(m, T) {
  const out = [];
  for (let x = 5; x <= m; x++) {
    const now = win(cum, x, 5), prev = win(cum, x - 1, 5);
    S.forEach((k, i) => { if (Math.abs(now[i]) >= T && Math.abs(prev[i]) < T) out.push([x, k, now[i]]); });
  }
  return out;
}
function render() {
  const T = Math.max(1, Number(document.getElementById("thr").value) || __THR__);
  const spot = D.spot[minute], lv = D.levels[minute];
  const idx = S.reduce((best, k, i) => Math.abs(k - spot) < Math.abs(S[best] - spot) ? i : best, 0), atm = S[idx];
  const rows = []; for (let j = 14; j >= -14; j--) { const i = idx + j; if (i >= 0 && i < S.length) rows.push(i); }
  const W = [5, 15, 30, 60].map(w => win(cum, minute, w));
  const ml15 = win(mlCum, minute, 15), C15 = win(cumC, minute, 15), P15 = win(cumP, minute, 15);
  document.getElementById("clock").textContent = hhmm(minute);
  document.getElementById("tsLabel").textContent = hhmm(minute) + " ET";
  document.getElementById("replayLabel").textContent = hhmm(minute);
  document.getElementById("spotVal").textContent = spot.toFixed(2);
  for (const [id, key] of [["pwVal", "pw"], ["zgVal", "flip"], ["cwVal", "cw"], ["absVal", "abs"]]) document.getElementById(id).textContent = lv[key] == null ? "—" : Math.round(lv[key]);
  let dayNet = 0, sg = 0, all = 0; for (let m = 0; m <= minute; m++) { sg += D.signed[m]; all += D.signed[m] + D.mlt[m] + D.oth[m]; }
  S.forEach((_, i) => dayNet += cum[minute][i]);
  document.getElementById("dayNet").textContent = fmt(dayNet) + " Δ"; document.getElementById("dayNet").className = "mono " + (dayNet >= 0 ? "reg-pos" : "reg-neg");
  document.getElementById("dayShare").textContent = Math.round(100 * sg / Math.max(1, all)) + " %";
  const al = alertsUpTo(minute, T);
  document.getElementById("alertCount").textContent = al.length;
  document.getElementById("alertList").innerHTML = al.slice(-12).reverse().map(([x, k, v]) => `<div>${hhmm(x)} · ${k} · <b class="${v > 0 ? "reg-pos" : "reg-neg"}">${fmt(v)} Δ en 5 min</b> · spot ${D.spot[x].toFixed(1)}</div>`).join("") || "—";
  const magnet = W.map(arr => rows.reduce((b, i) => Math.abs(arr[i]) > Math.abs(arr[b]) ? i : b, rows[0]));
  document.getElementById("imanValue").textContent = S[magnet[1]];
  ["m5", "m15", "m30", "m60"].forEach((id, c) => {
    let pos = 0, neg = 0; rows.forEach(i => W[c][i] > 0 ? pos += W[c][i] : neg += W[c][i]);
    document.getElementById(id).innerHTML = `<span class="pos">Alcista ${fmt(pos)}</span><span class="neg">Bajista ${fmt(neg)}</span><span>desde ${hhmm(Math.max(0, minute - [5, 15, 30, 60][c] + 1))}</span>`;
  });
  const maxAbs = W.map(arr => Math.max(1, ...rows.map(i => Math.abs(arr[i]))));
  const gmax = Math.max(1, ...rows.map(i => Math.abs(D.gex[minute][i] || 0)));
  let html = "";
  rows.forEach(i => {
    const k = S[i], g = D.gex[minute][i];
    const pill = g == null || Math.abs(g) < gmax * 0.01 ? "zero" : g >= 0 ? "pos" : "neg";
    const tags = [];
    if (lv.cw === k) tags.push('<span class="tag cw">CW</span>'); if (lv.pw === k) tags.push('<span class="tag pw">PW</span>');
    if (lv.abs === k) tags.push('<span class="tag zg">ABS</span>'); if (lv.flip != null && Math.abs(lv.flip - k) < 2.5) tags.push('<span class="tag zg">FLIP</span>');
    const cells = W.map((arr, c) => {
      const v = arr[i], width = Math.min(100, 100 * Math.abs(v) / maxAbs[c]);
      return `<td><div class="delta-cell">${magnet[c] === i && v ? '<span class="magnet-mark">🧲</span>' : ""}<div class="bar-track"><div class="bar-mid"></div><div class="bar-fill ${v >= 0 ? "pos" : "neg"}" style="width:${width / 2}%"></div></div><div class="pct ${v >= 0 ? "pos" : "neg"}" style="min-width:70px">${v ? fmt(v) : '<span class="na">0</span>'}</div></div></td>`;
    }).join("");
    const alertRow = Math.abs(W[0][i]) >= T;
    html += `<tr class="${k === atm ? "spot-row" : ""} ${alertRow ? "alert-row" : ""}" title="Flujo 15M: calls ${fmt(C15[i])} Δ · puts ${fmt(P15[i])} Δ"><td><div class="strike-cell">${k}${k === atm ? '<span class="spot-tag">SPOT</span>' : ""}</div></td><td><span class="gex-pill ${pill}">${fmtM(g)}</span></td>${cells}<td><span class="na">${ml15[i] ? ml15[i].toLocaleString("en-US") : "—"}</span></td><td><div class="nivel-tags">${tags.join("") || '<span class="na">—</span>'}</div></td></tr>`;
  });
  document.getElementById("tbody").innerHTML = html;
}
document.getElementById("slider").addEventListener("input", e => { minute = Number(e.target.value); render(); });
document.getElementById("thr").addEventListener("change", render);
document.getElementById("play").addEventListener("click", e => {
  if (timer) { clearInterval(timer); timer = null; e.target.textContent = "▶"; return; }
  if (minute >= N - 1) minute = 4;
  e.target.textContent = "■";
  timer = setInterval(() => { minute = Math.min(N - 1, minute + 1); document.getElementById("slider").value = minute; render(); if (minute >= N - 1) { clearInterval(timer); timer = null; e.target.textContent = "▶"; } }, 120);
});
render();
</script>
"""
body = body.replace("__SYMBOL__", SYMBOL).replace("__ROOT__", ROOT).replace("__DAY__", DAY).replace("__THR__", str(THRESHOLD))
html = f"<title>Cadena de flujo {SYMBOL}</title>\n" + style + "\n" + fonts + "\n" + body.replace("__DATA__", payload)
out = f"C:/Users/leste/Documents/Convexa/docs/prototipo_cadena_flujo_{SYMBOL}_{DAY}.html"
open(out, "w", encoding="utf-8").write(html)
print("written", out, round(len(html) / 1e6, 2), "MB")
