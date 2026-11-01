"""READ-ONLY. Preregistered TEST 1 (docs/preregistro_nivel_apertura_2026-10-29.txt, section 1): the brightest strikes at 09:35 as exit targets.

Per symbol (SPX NDX SPY QQQ IWM) and stored tactical session: at the first tactical cycle >= 09:35, for each layer (delta, ABS, calls, puts) the 3
strikes with the largest |value| above and below the price inside +-1.25 ATR. Per level: touch (within 0.05 %), first-touch time, overshoot after the
touch (points and Z), day high/low within 0.25 ATR; as an exit target for a 09:35 entry: hit rate, time to target, worst adverse move before it.
Baselines: (a) the other strikes of the same layer, same day, same side and same Z group (expectation over all of them = random draws);
(b) EMC / EMP; (c) fixed targets. Weather-style table by rank / Z group / side / hour, calibration (leave-one-day-out) and Brier score.
Plus the dark near-zero net-gamma band as a bounce zone vs equal-width bands. Deviations are printed at the top ("DESVÍOS")."""
import math
import random
import sys
from bisect import bisect_left
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, "V:/Convexa/repo")
sys.stdout.reconfigure(encoding="utf-8")  # the server console is cp1252: σ, ≥, ± would fail
from sqlalchemy import text

from backend.adapters.providers.thetadata.provider import _time_to_expiration_years
from backend.core.container import build_container
from backend.domain.use_cases.calculate_expected_move import (
    _calculate_variance,
    _select_atm_strike_and_forward,
    _select_k0,
    _select_otm_strikes,
    calculate_time_to_close_pct,
)

ET = ZoneInfo("America/New_York")
SYMBOLS = ("SPX", "NDX", "SPY", "QQQ", "IWM")
LAYERS = ("delta", "abs", "calls", "puts")
FIXED = {"SPX": (10, 20, 30), "NDX": (50, 100, 150), "SPY": (1, 2, 3), "QQQ": (1, 2, 3), "IWM": (1, 2, 3)}
TOUCH = 0.0005          # 0.05 % of the strike
NEAR_HL_ATR = 0.25      # day high / low within 0.25 ATR
WINDOW_ATR = 1.25       # heatmap window
BOUNCE = 0.0015         # 0.15 %
BOUNCE_MIN = 30
GAP_MIN = 10
Z_EDGES = (0.5, 1.0, 1.5)
Z_NAMES = ("<0.5", "0.5-1", "1-1.5", ">1.5")
HOURS = ((time(10, 0), "10:00"), (time(10, 30), "10:30"), (time(11, 0), "11:00"), (time(12, 0), "12:00"), (time(13, 0), "13:00"),
         (time(14, 0), "14:00"), (time(15, 0), "15:00"), (time(16, 0), "16:00"))
BOOT = 1000
RNG = random.Random(20261031)


def zbucket(z):
    a = abs(z)
    for i, edge in enumerate(Z_EDGES):
        if a < edge:
            return Z_NAMES[i]
    return Z_NAMES[-1]


def pct(x):
    return "  n/a" if x is None else f"{100 * x:5.1f}"


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else None


def boot_ci(groups):
    """groups = {day: [diff, ...]}; mean of all diffs and a 1,000-replicate bootstrap over days (2.5 / 97.5 %)."""
    days = [d for d in groups if groups[d]]
    if not days:
        return None, None, None
    allv = [v for d in days for v in groups[d]]
    means = []
    for _ in range(BOOT):
        pick = [RNG.choice(days) for _ in days]
        vals = [v for d in pick for v in groups[d]]
        means.append(sum(vals) / len(vals))
    means.sort()
    return sum(allv) / len(allv), means[int(0.025 * BOOT)], means[int(0.975 * BOOT) - 1]


class Path:
    """The session's prices from 09:35 to 16:00 with prefix / suffix extremes, so first touch, adverse move and overshoot are O(log n)."""

    def __init__(self, rows, t0):
        self.t = [r[0] for r in rows if r[0] >= t0]
        self.p = [r[1] for r in rows if r[0] >= t0]
        self.pmax, self.pmin = [], []
        hi, lo = -math.inf, math.inf
        for v in self.p:
            hi, lo = max(hi, v), min(lo, v)
            self.pmax.append(hi)
            self.pmin.append(lo)
        n = len(self.p)
        self.smax, self.smin = [0.0] * n, [0.0] * n
        hi, lo = -math.inf, math.inf
        for i in range(n - 1, -1, -1):
            hi, lo = max(hi, self.p[i]), min(lo, self.p[i])
            self.smax[i], self.smin[i] = hi, lo

    def first_touch(self, level, side):
        """Index of the first price within TOUCH of `level` coming from `side` (+1 level above the entry, -1 below), or None."""
        if side > 0:
            target = level * (1 - TOUCH)
            if not self.pmax or self.pmax[-1] < target:
                return None
            return bisect_left(self.pmax, target)
        target = level * (1 + TOUCH)
        if not self.pmin or self.pmin[-1] > target:
            return None
        neg = [-v for v in self.pmin] if not hasattr(self, "_negmin") else self._negmin
        self._negmin = neg
        return bisect_left(neg, -target)


def evaluate(path, entry, level, side, sigma_pts, atr, day_hi, day_lo, t0):
    """Metrics of one level as an exit target for an entry at `entry` (09:35) in direction `side`."""
    i = path.first_touch(level, side)
    near = abs((day_hi if side > 0 else day_lo) - level) <= NEAR_HL_ATR * atr
    if i is None:
        adverse = (entry - path.pmin[-1]) if side > 0 else (path.pmax[-1] - entry)
        return {"touch": False, "minutes": None, "clock": None, "over_pts": None, "over_z": None, "near_hl": near, "adverse": max(adverse, 0.0)}
    adverse = (entry - path.pmin[i]) if side > 0 else (path.pmax[i] - entry)
    over = (path.smax[i] - level) if side > 0 else (level - path.smin[i])
    over = max(over, 0.0)
    clock = path.t[i]
    return {"touch": True, "minutes": (clock - t0).total_seconds() / 60, "clock": clock.time(), "over_pts": over,
            "over_z": over / sigma_pts if sigma_pts else None, "near_hl": near, "adverse": max(adverse, 0.0)}


def band_reaction(rows, lo, hi):
    """First touch of [lo, hi] from outside after 09:35 ('rows' = (t, p) from 09:35): 'bounce' = back >= 0.15 % on the side it came from within 30 min,
    'cross' = >= 0.15 % beyond the other edge, 'touch' = neither, None = never touched."""
    b_lo, b_hi = lo * (1 - TOUCH), hi * (1 + TOUCH)
    outside_side = None
    for idx, (t, p) in enumerate(rows):
        if p > b_hi:
            outside_side = 1
            continue
        if p < b_lo:
            outside_side = -1
            continue
        if outside_side is None:
            continue  # started inside the band: wait until it leaves
        limit = t + timedelta(minutes=BOUNCE_MIN)
        for u, q in rows[idx:]:
            if u > limit:
                break
            if outside_side > 0 and q >= hi * (1 + BOUNCE):
                return "bounce"
            if outside_side < 0 and q <= lo * (1 - BOUNCE):
                return "bounce"
            if outside_side > 0 and q <= lo * (1 - BOUNCE):
                return "cross"
            if outside_side < 0 and q >= hi * (1 + BOUNCE):
                return "cross"
        return "touch"
    return None


def atr_for(bars, day):
    prior = [b for b in bars if b[0] < day][-15:]
    if len(prior) < 15:
        return None
    trs = [max(c[2] - c[3], abs(c[2] - p[4]), abs(c[3] - p[4])) for p, c in zip(prior, prior[1:])]
    return sum(trs) / len(trs)


def max_gap_minutes(times, start, end):
    pts = [start] + sorted(t for t in times if start <= t <= end) + [end]
    return max((b - a).total_seconds() / 60 for a, b in zip(pts, pts[1:]))


def top3(values, strikes):
    return sorted(strikes, key=lambda k: -abs(values[k]))[:3]


c = build_container()
records = []        # one per (symbol, day, layer, side, strike) bright or other strike
specials = []       # EMC / EMP / fixed targets
bands = []          # dark band and comparison bands
excluded = []
recon_check = []    # delta reconstruction vs stored, days where both exist
day_info = []

with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    s.execute(text("SET LOCAL statement_timeout='600s'"))
    uid = {sym: i for i, sym in s.execute(text("select id, symbol from underlyings"))}
    for sym in SYMBOLS:
        u = uid[sym]
        bars = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in s.execute(text(
            "select date, open, high, low, close from daily_bars where underlying_id = :u order by date"), {"u": u})]
        heads = s.execute(text("""
            select (time at time zone 'America/New_York')::date d, min(time) from gamma_aggregates
            where underlying_id = :u and view = 'tactical' and (time at time zone 'America/New_York')::time between '09:35' and '09:45'
            group by 1 order by 1"""), {"u": u}).all()
        for day, g_time in heads:
            g_et = g_time.astimezone(ET)
            open_dt = datetime.combine(day, time(9, 30), ET)
            ten = datetime.combine(day, time(10, 0), ET)
            close_dt = datetime.combine(day, time(16, 0), ET)
            rows = [(t.astimezone(ET), float(p)) for t, p in s.execute(text(
                "select time, price from market_snapshots where underlying_id = :u and time between :a and :b order by time"),
                {"u": u, "a": open_dt, "b": close_dt})]
            cycles = [t.astimezone(ET) for (t,) in s.execute(text(
                "select time from gamma_aggregates where underlying_id = :u and view = 'tactical' and time between :a and :b"),
                {"u": u, "a": open_dt, "b": ten})]
            gp = max_gap_minutes([r[0] for r in rows], open_dt, ten) if rows else 999
            gc = max_gap_minutes(cycles, open_dt, ten) if cycles else 999
            if gp > GAP_MIN or gc > GAP_MIN:
                excluded.append(f"{sym} {day}: max gap prices {gp:.1f} min, cycles {gc:.1f} min")
                continue
            atr = atr_for(bars, day)
            if atr is None:
                excluded.append(f"{sym} {day}: fewer than 15 daily bars for the ATR")
                continue
            before = [r for r in rows if r[0] <= g_et]
            entry = before[-1][1] if before else rows[0][1]
            session_open = rows[0][1]
            day_hi, day_lo = max(r[1] for r in rows), min(r[1] for r in rows)
            path = Path(rows, g_et)

            items = s.execute(text("""
                select strike, call_gamma_exposure, put_gamma_exposure, delta_exposure from gamma_aggregate_items
                where underlying_id = :u and view = 'tactical' and time = :t"""), {"u": u, "t": g_time}).all()
            # the full chain snapshot written by the same cycle (largest snapshot in the 3 minutes before the cycle time)
            snap_t = s.execute(text("""
                select time from option_chain_snapshots where underlying_id = :u and time between :a and :b
                group by time order by count(*) desc, time desc limit 1"""), {"u": u, "a": g_time - timedelta(seconds=180), "b": g_time + timedelta(seconds=2)}).scalar()
            chain = s.execute(text("""
                select c.strike, c.expiration, c.contract_type, o.bid, o.ask, o.iv, o.delta, o.open_interest from option_chain_snapshots o
                join option_contracts c on c.id = o.contract_id where o.underlying_id = :u and o.time = :t"""), {"u": u, "t": snap_t}).all() if snap_t else []
            is_call = lambda ct: "CALL" in str(ct).upper() or str(ct).upper() == "C"

            # sigma: ATM IV of the nearest listed expiration of the view (0DTE on these days) from the cycle's chain; T as ThetaData computes it
            exps = sorted({r[1] for r in chain if r[1] >= day})
            sigma = T = None
            em_hi = em_lo = None
            if exps:
                near_exp = exps[0]
                same = [r for r in chain if r[1] == near_exp]
                ks = sorted({float(r[0]) for r in same})
                atm = min(ks, key=lambda k: abs(k - entry))
                ivs = [float(r[5]) for r in same if float(r[0]) == atm and r[5] is not None and float(r[5]) > 0]
                if ivs:
                    sigma = sum(ivs) / len(ivs)
                T = float(_time_to_expiration_years(near_exp, g_et))
                # EMC / EMP exactly as the live pill: variance-swap IV of the nearest expiration, anchored on the session's first price
                calls = {r[0]: SimpleNamespace(bid=r[3] or Decimal(0), ask=r[4] or Decimal(0)) for r in same if is_call(r[2])}
                puts = {r[0]: SimpleNamespace(bid=r[3] or Decimal(0), ask=r[4] or Decimal(0)) for r in same if not is_call(r[2])}
                ttc = calculate_time_to_close_pct(g_et)
                dte = max((near_exp - day).days, 0)
                yf = ttc / 100 / 365 if dte == 0 else Decimal(dte) / 365
                found = _select_atm_strike_and_forward(calls, puts)
                var = None
                if found:
                    k0 = _select_k0(sorted(set(calls) | set(puts)), found[1], found[0])
                    var = _calculate_variance(_select_otm_strikes(calls, puts, k0), k0, found[1], yf)
                em_iv = float(var.sqrt()) if var is not None and var > 0 else sigma
                if em_iv:
                    move = session_open * em_iv * math.sqrt(float(yf))
                    em_hi, em_lo = session_open + move, session_open - move
            if not sigma or not T:
                excluded.append(f"{sym} {day}: no ATM IV in the cycle's chain snapshot")
                continue
            sigma_pts = entry * sigma * math.sqrt(T)
            zf = lambda k: (entry - k) / sigma_pts

            # layers
            stored_delta = {float(r[0]): float(r[3]) for r in items if r[3] is not None}
            recon = defaultdict(float)
            for r in chain:
                if day <= r[1] <= day + timedelta(days=2) and r[6] is not None and r[7] is not None:
                    recon[float(r[0])] += float(r[6]) * float(r[7])
            layer_vals = {
                "abs": {float(r[0]): abs(float(r[1])) + abs(float(r[2])) for r in items},
                "calls": {float(r[0]): float(r[1]) for r in items},
                "puts": {float(r[0]): float(r[2]) for r in items},
            }
            strikes_all = sorted(layer_vals["abs"])
            if len(stored_delta) == len(strikes_all) and strikes_all:
                layer_vals["delta"] = stored_delta
                delta_src = "stored"
            else:
                layer_vals["delta"] = {k: recon.get(k, 0.0) for k in strikes_all}
                delta_src = "rebuilt"
            lo_w, hi_w = entry - WINDOW_ATR * atr, entry + WINDOW_ATR * atr
            window = [k for k in strikes_all if lo_w <= k <= hi_w]
            if stored_delta and len(stored_delta) == len(strikes_all):
                for side in (1, -1):
                    ks = [k for k in window if (k > entry if side > 0 else k < entry)]
                    if ks:
                        a = set(top3(stored_delta, ks))
                        b = set(top3({k: recon.get(k, 0.0) for k in ks}, ks))
                        recon_check.append((sym, day, len(a & b), len(a)))
            day_info.append(f"{sym} {day} cycle {g_et:%H:%M:%S} price {entry:g} ATR {atr:.2f} IV {sigma:.3f} T {T * 365 * 24:.2f} h 1-Z = {sigma_pts:.2f} pts "
                            f"window strikes {len(window)} delta {delta_src} EMC {em_hi and round(em_hi, 2)} EMP {em_lo and round(em_lo, 2)}")

            for layer in LAYERS:
                vals = layer_vals[layer]
                for side in (1, -1):
                    ks = [k for k in window if (k > entry if side > 0 else k < entry)]
                    bright = top3(vals, ks)
                    for k in ks:
                        m = evaluate(path, entry, k, side, sigma_pts, atr, day_hi, day_lo, g_et)
                        rank = bright.index(k) + 1 if k in bright else 0
                        records.append({"sym": sym, "day": day, "layer": layer, "side": side, "strike": k, "rank": rank, "z": zf(k),
                                        "zb": zbucket(zf(k)), "dist": abs(k - entry), "atr_d": abs(k - entry) / atr, **m})
            for name, k, side in (("EMC", em_hi, 1), ("EMP", em_lo, -1)):
                if k is not None and (k - entry) * side > 0:
                    specials.append({"sym": sym, "day": day, "kind": name, "side": side, "z": zf(k), "zb": zbucket(zf(k)), "dist": abs(k - entry),
                                     **evaluate(path, entry, k, side, sigma_pts, atr, day_hi, day_lo, g_et)})
            for pts in FIXED[sym]:
                for side in (1, -1):
                    k = entry + side * pts
                    specials.append({"sym": sym, "day": day, "kind": f"fixed {pts}", "side": side, "z": zf(k), "zb": zbucket(zf(k)), "dist": pts,
                                     **evaluate(path, entry, k, side, sigma_pts, atr, day_hi, day_lo, g_et)})
            # dark band: adjacent window strikes where net gamma (call + put) changes sign; comparison = adjacent pairs without a sign change
            net = {k: layer_vals["calls"][k] + layer_vals["puts"][k] for k in window}
            after = [(t, p) for t, p in rows if t >= g_et]
            for a, b in zip(window, window[1:]):
                dark = net[a] * net[b] < 0
                bands.append({"sym": sym, "day": day, "dark": dark, "lo": a, "hi": b, "z": zf((a + b) / 2), "out": band_reaction(after, a, b)})
        print(f"... {sym} done ({sum(1 for r in records if r['sym'] == sym and r['layer'] == 'abs' and r['rank'] == 1)} rank-1 ABS levels)", flush=True)
    s.rollback()

# ------------------------------------------------------------------ report
print()
print("PRUEBA 1 DEL PREREGISTRO (docs/preregistro_nivel_apertura_2026-10-29.txt) — corrida 2026-10-31, solo lectura")
print("=" * 110)
print("""DESVÍOS Y ACLARACIONES (2026-10-31, escritos antes de leer los resultados):
 D1. Delta por strike: guardado solo desde el 2026-10-19 (no ~10-16). Del 09-28 al 10-16 se reconstruye desde la foto de cadena del mismo ciclo:
     suma de delta x OI por strike de los vencimientos de hoy a hoy+2 días de calendario (la regla táctica guardada esos días). El factor
     100 x precio no cambia el orden del brillo. Abajo se mide cuánto coincide la reconstrucción con lo guardado en los días que tienen ambos.
 D2. Ventana: ±1,25 ATR (ATR 14 días de daily_bars, como el heatmap) cortada a los strikes que la vista táctica guardó ese ciclo; strikes
     fuera de lo guardado no existen en los datos (IWM guarda ~11 strikes).
 D3. σ de Z: IV ATM (promedio call/put, sin IV=0) del vencimiento MÁS CERCANO de la vista táctica (0DTE todos los días) en la foto de cadena
     del ciclo de las 09:35; T = _time_to_expiration_years de ese vencimiento. La vista táctica mezcla hasta 3 vencimientos; se usa el primero.
 D4. Base (a) "strikes al azar a la misma Z": todos los demás strikes de la ventana de esa capa (no top 3), mismo día, mismo lado y mismo
     grupo de Z; se usa su tasa media (= infinitos sorteos). Se muestra también la |Z| media de cada grupo para ver si la distancia sesga.
 D5. Criterio "útil": se evalúa por capa; nivel 1º de brillo menos base (a) del mismo día/lado/grupo de Z; bootstrap por día (1.000).
 D6. Calibración: la probabilidad de cada nivel = su tasa de toque en la celda (rango x grupo de Z x lado, todas las capas y símbolos)
     calculada SIN ese día (dejar un día fuera); Brier comparado con un pronóstico solo por grupo de Z.
 D7. EMC/EMP: recalculados con las mismas funciones del pill en vivo (réplica de varianza del vencimiento más cercano, ancla = primer precio
     de la sesión) sobre la foto de cadena de las 09:35.
 D8. Vistas 0dte / 0dte_volume: solo 4 días (10-27 a 10-30); no se analizan aparte (muy pocos casos).
 D9. Banda oscura: pares de strikes vecinos de la ventana donde la gamma neta (call + put) cambia de signo; comparación = pares vecinos sin
     cambio de signo (mismo ancho de un escalón). Toque = el precio entra en la banda viniendo de afuera; rebote = vuelve ≥ 0,15 % hacia su
     lado en 30 min; cruce = sale ≥ 0,15 % por el otro lado.""")
print()
print("DÍAS EXCLUIDOS (huecos > 10 min entre 09:30 y 10:00, o sin datos):")
for line in excluded or ["(ninguno)"]:
    print("  " + line)
print()
print("SESIONES USADAS")
for line in day_info:
    print("  " + line)
if recon_check:
    hit = sum(a for _, _, a, _ in recon_check)
    tot = sum(b for _, _, _, b in recon_check)
    print(f"\nD1 control: delta reconstruido vs guardado (10-19 a 10-30), top 3 por lado: coinciden {hit} de {tot} strikes ({hit / tot:.0%})")
    for sym in SYMBOLS:
        rows_ = [x for x in recon_check if x[0] == sym]
        if rows_:
            print(f"   {sym}: {sum(x[2] for x in rows_)}/{sum(x[3] for x in rows_)}")


def rate(xs, key="touch"):
    xs = list(xs)
    return (sum(1 for x in xs if x[key]) / len(xs)) if xs else None


print("\n" + "=" * 110)
print("1) TASA DE TOQUE POR SÍMBOLO, CAPA Y RANGO (todos los grupos de Z juntos) — n = niveles")
print(f"{'sím':4} {'capa':6} | {'1º':>13} | {'2º':>13} | {'3º':>13} | {'resto (base a)':>15} | |Z| media 1º / resto")
for sym in SYMBOLS:
    for layer in LAYERS:
        rs = [r for r in records if r["sym"] == sym and r["layer"] == layer]
        cells = []
        for rank in (1, 2, 3, 0):
            sub = [r for r in rs if r["rank"] == rank]
            cells.append(f"{pct(rate(sub))} % n={len(sub):3d}")
        z1 = [abs(r["z"]) for r in rs if r["rank"] == 1]
        z0 = [abs(r["z"]) for r in rs if r["rank"] == 0]
        print(f"{sym:4} {layer:6} | {cells[0]:>13} | {cells[1]:>13} | {cells[2]:>13} | {cells[3]:>15} | "
              f"{(sum(z1) / len(z1)) if z1 else float('nan'):.2f} / {(sum(z0) / len(z0)) if z0 else float('nan'):.2f}")

print("\n" + "=" * 110)
print("2) CRITERIO PREREGISTRADO: 1º de brillo − base (a) del mismo día, lado y grupo de Z (puntos porcentuales), IC 95 % bootstrap por día")
print("   'útil' si diferencia ≥ 10 pp y el IC no cruza cero, en ≥ 3 de 5 símbolos (por capa)")
verdict = defaultdict(int)
for layer in LAYERS:
    for sym in SYMBOLS:
        rs = [r for r in records if r["sym"] == sym and r["layer"] == layer]
        groups = defaultdict(list)
        pairs = 0
        for r in rs:
            if r["rank"] != 1:
                continue
            base = [b for b in rs if b["rank"] == 0 and b["day"] == r["day"] and b["side"] == r["side"] and b["zb"] == r["zb"]]
            if not base:
                continue
            groups[r["day"]].append((1.0 if r["touch"] else 0.0) - rate(base))
            pairs += 1
        mean, lo, hi = boot_ci(groups)
        ok = mean is not None and mean >= 0.10 and lo > 0
        verdict[layer] += ok
        print(f"   {layer:6} {sym:4}: pares {pairs:3d}  diferencia {pct(mean)} pp  IC [{pct(lo)}, {pct(hi)}]  {'CUMPLE' if ok else 'no cumple'}")
    print(f"   -> {layer}: {verdict[layer]} de 5 símbolos cumplen  => {'ÚTIL' if verdict[layer] >= 3 else 'NO útil'} según el preregistro\n")

print("=" * 110)
print("3) POR GRUPO DE Z Y LADO (todas las capas y símbolos): toque 1º / 2º / 3º / resto")
for side, sname in ((1, "arriba"), (-1, "abajo")):
    for zb in Z_NAMES:
        rs = [r for r in records if r["side"] == side and r["zb"] == zb]
        if not rs:
            continue
        cells = " | ".join(f"{pct(rate([r for r in rs if r['rank'] == k]))} % n={sum(1 for r in rs if r['rank'] == k):4d}" for k in (1, 2, 3, 0))
        print(f"   {sname:6} Z {zb:6}: {cells}")

print("\n" + "=" * 110)
print("4) COMO OBJETIVO DE SALIDA de una entrada a las 09:35 (todas las capas): acierto, minutos hasta el objetivo (mediana),")
print("   peor movimiento en contra antes de llegar (mediana, en ATR), exceso después del toque (mediana, en Z), máx/mín del día cerca (≤ 0,25 ATR)")


def exit_line(label, rs, atr_of):
    rs = list(rs)
    if not rs:
        return
    adv = [r["adverse"] / atr_of[(r["sym"], r["day"])] for r in rs if r["touch"]]
    print(f"   {label:28} n={len(rs):4d} acierto {pct(rate(rs))} %  min {median([r['minutes'] for r in rs]) or float('nan'):6.1f}  "
          f"en contra {median(adv) or 0:.2f} ATR  exceso {median([r['over_z'] for r in rs if r['touch']]) or 0:.2f} Z  "
          f"máx/mín cerca {pct(rate(rs, 'near_hl'))} %")


atr_of = {}
for line in day_info:
    parts = line.split()
    atr_of[(parts[0], date.fromisoformat(parts[1]))] = float(parts[parts.index("ATR") + 1])
for sym in SYMBOLS:
    print(f"  {sym}")
    for rank in (1, 2, 3):
        exit_line(f"brillo {rank}º (4 capas)", (r for r in records if r["sym"] == sym and r["rank"] == rank), atr_of)
    exit_line("base (a) resto", (r for r in records if r["sym"] == sym and r["rank"] == 0), atr_of)
    for kind in ["EMC", "EMP"] + [f"fixed {p}" for p in FIXED[sym]]:
        exit_line(f"base {kind}", (r for r in specials if r["sym"] == sym and r["kind"] == kind), atr_of)

print("\n   Bases (b) y (c) frente al brillo en el MISMO grupo de Z (todas las capas y símbolos):")
for zb in Z_NAMES:
    b1 = [r for r in records if r["rank"] == 1 and r["zb"] == zb]
    ba = [r for r in records if r["rank"] == 0 and r["zb"] == zb]
    em = [r for r in specials if r["kind"] in ("EMC", "EMP") and r["zb"] == zb]
    fx = [r for r in specials if r["kind"].startswith("fixed") and r["zb"] == zb]
    print(f"   Z {zb:6}: brillo 1º {pct(rate(b1))} % (n={len(b1)})  base a {pct(rate(ba))} % (n={len(ba)})  EMC/EMP {pct(rate(em))} % (n={len(em)})  "
          f"fijos {pct(rate(fx))} % (n={len(fx)})")

print("\n" + "=" * 110)
print("5) TABLA TIPO PRONÓSTICO: probabilidad de tocar el nivel ANTES de cada hora (todas las capas y símbolos; rango x grupo de Z x lado)")
print("   " + f"{'celda':26} {'n':>5} " + " ".join(f"{h:>6}" for _, h in HOURS))
for rank in (1, 2, 3, 0):
    for side, sname in ((1, "arriba"), (-1, "abajo")):
        for zb in Z_NAMES:
            rs = [r for r in records if r["rank"] == rank and r["side"] == side and r["zb"] == zb]
            if len(rs) < 5:
                continue
            cum = [sum(1 for r in rs if r["touch"] and r["clock"] <= h) / len(rs) for h, _ in HOURS]
            lab = f"{'resto' if rank == 0 else str(rank) + 'º'} {sname} Z {zb}"
            print(f"   {lab:26} {len(rs):5d} " + " ".join(f"{100 * x:5.0f}%" for x in cum))

print("\n" + "=" * 110)
print("6) CALIBRACIÓN (dejar un día fuera): pronóstico por celda rango x grupo de Z x lado vs lo que pasó")
cell = lambda r: (r["rank"] > 0 and r["rank"], r["zb"], r["side"])
by_day_cell = defaultdict(lambda: [0, 0])
by_day_z = defaultdict(lambda: [0, 0])
for r in records:
    by_day_cell[(r["day"], cell(r))][0] += r["touch"]
    by_day_cell[(r["day"], cell(r))][1] += 1
    by_day_z[(r["day"], r["zb"])][0] += r["touch"]
    by_day_z[(r["day"], r["zb"])][1] += 1
tot_cell, tot_z = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
for (d, k), (a, n) in by_day_cell.items():
    tot_cell[k][0] += a
    tot_cell[k][1] += n
for (d, k), (a, n) in by_day_z.items():
    tot_z[k][0] += a
    tot_z[k][1] += n
bins = defaultdict(lambda: [0, 0, 0.0])
brier = brier_z = 0.0
nb = 0
for r in records:
    a, n = tot_cell[cell(r)]
    da, dn = by_day_cell[(r["day"], cell(r))]
    if n - dn <= 0:
        continue
    f = (a - da) / (n - dn)
    za, zn = tot_z[r["zb"]]
    dza, dzn = by_day_z[(r["day"], r["zb"])]
    fz = (za - dza) / (zn - dzn) if zn - dzn > 0 else f
    o = 1.0 if r["touch"] else 0.0
    brier += (f - o) ** 2
    brier_z += (fz - o) ** 2
    nb += 1
    b = min(int(f * 10), 9)
    bins[b][0] += 1
    bins[b][1] += o
    bins[b][2] += f
print(f"   {'pronóstico':12} {'n':>6} {'dijo':>7} {'pasó':>7}")
for b in sorted(bins):
    n, o, f = bins[b]
    print(f"   {b * 10:3d}-{b * 10 + 10:3d} %   {n:6d} {100 * f / n:6.1f}% {100 * o / n:6.1f}%")
if nb:
    print(f"   Brier con celdas de brillo: {brier / nb:.4f}   Brier solo con grupo de Z: {brier_z / nb:.4f}   (más bajo = mejor; n = {nb})")

print("\n" + "=" * 110)
print("7) HORA DEL PRIMER TOQUE (niveles tocados, todas las capas): reparto por tramo")
for rank in (1, 0):
    rs = [r for r in records if (r["rank"] == 1 if rank else r["rank"] == 0) and r["touch"]]
    prev = time(9, 35)
    parts = []
    for h, name in HOURS:
        n = sum(1 for r in rs if prev < r["clock"] <= h or (prev == time(9, 35) and r["clock"] <= h and r["clock"] >= prev))
        parts.append(f"{prev:%H:%M}-{name} {n / len(rs) * 100 if rs else 0:4.0f}%")
        prev = h
    print(f"   {'1º brillo' if rank else 'resto':9} (n={len(rs)}): " + "  ".join(parts))

print("\n" + "=" * 110)
print("8) BANDA OSCURA (gamma neta cerca de cero entre verde y rojo) como zona de rebote vs bandas de igual ancho")
for sym in list(SYMBOLS) + ["TODOS"]:
    sel = [b for b in bands if sym == "TODOS" or b["sym"] == sym]
    out = []
    groups = defaultdict(list)
    for dark in (True, False):
        bs = [b for b in sel if b["dark"] == dark]
        touched = [b for b in bs if b["out"]]
        bounces = sum(1 for b in touched if b["out"] == "bounce")
        crosses = sum(1 for b in touched if b["out"] == "cross")
        out.append(f"{'oscura' if dark else 'otras '} n={len(bs):4d} tocadas {len(touched):4d} rebote {pct(bounces / len(touched) if touched else None)} % "
                   f"cruce {pct(crosses / len(touched) if touched else None)} %")
    days = {b["day"] for b in sel}
    for d in days:
        other = [b for b in sel if not b["dark"] and b["day"] == d and b["out"]]
        if not other:
            continue
        base = sum(1 for b in other if b["out"] == "bounce") / len(other)
        for b in sel:
            if b["dark"] and b["day"] == d and b["out"]:
                groups[d].append((1.0 if b["out"] == "bounce" else 0.0) - base)
    mean, lo, hi = boot_ci(groups)
    print(f"   {sym:5}: {out[0]} | {out[1]} | diferencia rebote {pct(mean)} pp IC [{pct(lo)}, {pct(hi)}]")

print("\nFin. Solo lectura; nada se convierte en alerta o señal sin otro sí de Lester.")
