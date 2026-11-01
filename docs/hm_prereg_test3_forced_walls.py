"""READ-ONLY. Preregistered TEST 3 (docs/preregistro_nivel_apertura_2026-10-29.txt, section 3, prueba (b)): walls as they are vs forced to each side.

(1) stored tactical Call Wall / Put Wall (net gamma, as computed today); (2) forced, rebuilt from the same cycle's stored items: Call Wall = largest
positive net gamma strictly ABOVE the price, Put Wall = most negative net gamma strictly BELOW it. The ABS is not touched.
Metrics: those of test 1 for the walls of the 09:35 cycle as exit targets (touch within 0.05 %, minutes, worst adverse move, overshoot in Z, day
high/low within 0.25 ATR) and reaction over the whole session (each run of the same wall level: first touch, then bounce >= 0.15 % back to its
side within 30 min / cross >= 0.15 % / plain touch). Bootstrap (1,000) by day of the difference stored - forced."""
import math
import random
import sys
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, "V:/Convexa/repo")
sys.stdout.reconfigure(encoding="utf-8")
from sqlalchemy import text

from backend.adapters.providers.thetadata.provider import _time_to_expiration_years
from backend.core.container import build_container

ET = ZoneInfo("America/New_York")
SYMBOLS = ("SPX", "NDX", "SPY", "QQQ", "IWM")
TOUCH, BOUNCE, BOUNCE_MIN, NEAR_HL_ATR, GAP_MIN, BOOT = 0.0005, 0.0015, 30, 0.25, 10, 1000
RNG = random.Random(20261031)


def max_gap_minutes(times, start, end):
    pts = [start] + sorted(t for t in times if start <= t <= end) + [end]
    return max((b - a).total_seconds() / 60 for a, b in zip(pts, pts[1:]))


def atr_for(bars, day):
    prior = [b for b in bars if b[0] < day][-15:]
    if len(prior) < 15:
        return None
    trs = [max(c[2] - c[3], abs(c[2] - p[4]), abs(c[3] - p[4])) for p, c in zip(prior, prior[1:])]
    return sum(trs) / len(trs)


def day_sigma(s, u, day, entry, g_et):
    """ATM IV (call/put average, IV > 0) of the nearest expiration from the largest chain snapshot in 09:35-10:00 that has one; T at `g_et`."""
    a, b = datetime.combine(day, time(9, 33), ET), datetime.combine(day, time(10, 0), ET)
    snaps = [t for (t,) in s.execute(text("""select time from option_chain_snapshots where underlying_id = :u and time between :a and :b
        group by time having count(*) > 200 order by time"""), {"u": u, "a": a, "b": b})]
    for t in snaps:
        chain = s.execute(text("""select c.strike, c.expiration, o.iv from option_chain_snapshots o join option_contracts c on c.id = o.contract_id
            where o.underlying_id = :u and o.time = :t and c.expiration >= :d"""), {"u": u, "t": t, "d": day}).all()
        exps = sorted({r[1] for r in chain})
        if not exps:
            continue
        same = [r for r in chain if r[1] == exps[0]]
        atm = min({float(r[0]) for r in same}, key=lambda k: abs(k - entry))
        ivs = [float(r[2]) for r in same if float(r[0]) == atm and r[2] is not None and float(r[2]) > 0]
        if ivs:
            return sum(ivs) / len(ivs), float(_time_to_expiration_years(exps[0], g_et)), t.astimezone(ET)
    return None, None, None


def exit_metrics(times, prices, level, side, entry, sigma_pts, atr, day_hi, day_lo, t0):
    i0 = bisect_left(times, t0)
    hit = None
    worst = 0.0
    for i in range(i0, len(prices)):
        p = prices[i]
        if (side > 0 and p >= level * (1 - TOUCH)) or (side < 0 and p <= level * (1 + TOUCH)):
            hit = i
            break
        worst = max(worst, (entry - p) if side > 0 else (p - entry))
    near = abs((day_hi if side > 0 else day_lo) - level) <= NEAR_HL_ATR * atr
    if hit is None:
        return {"touch": 0, "minutes": None, "adverse": worst, "over_z": None, "near": near}
    rest = prices[hit:]
    over = (max(rest) - level) if side > 0 else (level - min(rest))
    return {"touch": 1, "minutes": (times[hit] - t0).total_seconds() / 60, "adverse": worst, "over_z": max(over, 0) / sigma_pts, "near": near}


def reaction(times, prices, level, start, end):
    """First touch inside [start, end] (the run of this wall level): 'bounce' / 'cross' / 'touch' within 30 min after it, None = not touched."""
    band, away = level * TOUCH, level * BOUNCE
    i0, i1 = bisect_left(times, start), bisect_right(times, end)
    for i in range(i0, i1):
        if abs(prices[i] - level) > band:
            continue
        j = i - 1
        while j >= i0 and abs(prices[j] - level) <= band:
            j -= 1
        if j < i0:
            return None if i == i0 else None  # the run starts with price already on the level: no approach to judge
        side = 1 if prices[j] > level else -1
        k_end = bisect_right(times, times[i] + timedelta(minutes=BOUNCE_MIN))
        for k in range(i, k_end):
            q = prices[k]
            if (q - level) * side >= away:
                return "bounce"
            if (q - level) * side <= -away:
                return "cross"
        return "touch"
    return None


def runs(times, values):
    out, start = [], 0
    for i in range(1, len(values) + 1):
        if i == len(values) or values[i] != values[start]:
            if values[start] is not None:
                out.append((times[start], times[i - 1] if i < len(values) else times[i - 1] + timedelta(minutes=2), values[start]))
            start = i
    return out


def boot_ci(groups):
    days = [d for d in groups if groups[d]]
    if not days:
        return None, None, None
    allv = [v for d in days for v in groups[d]]
    reps = []
    for _ in range(BOOT):
        vals = [v for d in (RNG.choice(days) for _ in days) for v in groups[d]]
        reps.append(sum(vals) / len(vals))
    reps.sort()
    return sum(allv) / len(allv), reps[int(0.025 * BOOT)], reps[int(0.975 * BOOT) - 1]


c = build_container()
exits = []        # one per (sym, day, kind, wall)
reacts = []       # one per run of a wall level
agree = defaultdict(lambda: [0, 0])
wrong_side = defaultdict(lambda: [0, 0])
excluded = []
used = []
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    s.execute(text("SET LOCAL statement_timeout='600s'"))
    uid = {sym: i for i, sym in s.execute(text("select id, symbol from underlyings"))}
    for sym in SYMBOLS:
        u = uid[sym]
        bars = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in s.execute(text(
            "select date, open, high, low, close from daily_bars where underlying_id = :u order by date"), {"u": u})]
        dates = [d for (d,) in s.execute(text("""select distinct (time at time zone 'America/New_York')::date from gamma_aggregates
            where underlying_id = :u and view = 'tactical' order by 1"""), {"u": u})]
        for day in dates:
            o, t935, ten, cl = (datetime.combine(day, time(*hm), ET) for hm in ((9, 30), (9, 35), (10, 0), (16, 0)))
            cyc = [(t.astimezone(ET), None if cw is None else float(cw), None if pw is None else float(pw)) for t, cw, pw in s.execute(text("""
                select time, call_wall, put_wall from gamma_aggregates where underlying_id = :u and view = 'tactical' and time between :a and :b order by time"""),
                {"u": u, "a": o, "b": cl})]
            rows = [(t.astimezone(ET), float(p)) for t, p in s.execute(text(
                "select time, price from market_snapshots where underlying_id = :u and time between :a and :b order by time"), {"u": u, "a": o, "b": cl})]
            if not cyc or not rows:
                excluded.append(f"{sym} {day}: no cycles or prices")
                continue
            gp, gc = max_gap_minutes([r[0] for r in rows], o, ten), max_gap_minutes([x[0] for x in cyc], o, ten)
            if gp > GAP_MIN or gc > GAP_MIN:
                excluded.append(f"{sym} {day}: max gap prices {gp:.1f} min, cycles {gc:.1f} min (09:30-10:00)")
                continue
            atr = atr_for(bars, day)
            first = next((x for x in cyc if x[0] >= t935), None)
            if atr is None or first is None or first[0] > datetime.combine(day, time(9, 45), ET):
                excluded.append(f"{sym} {day}: no ATR or no tactical cycle 09:35-09:45")
                continue
            times = [r[0] for r in rows]
            prices = [r[1] for r in rows]
            price_at = lambda t: prices[max(bisect_right(times, t) - 1, 0)]
            entry = price_at(first[0])
            sigma, T, snap = day_sigma(s, u, day, entry, first[0])
            if sigma is None:
                excluded.append(f"{sym} {day}: no ATM IV 09:33-10:00")
                continue
            sigma_pts = entry * sigma * math.sqrt(T)
            items = defaultdict(dict)
            for t, k, cg, pg in s.execute(text("""select time, strike, call_gamma_exposure, put_gamma_exposure from gamma_aggregate_items
                where underlying_id = :u and view = 'tactical' and time between :a and :b"""), {"u": u, "a": o, "b": cl}):
                items[t.astimezone(ET)][float(k)] = float(cg) + float(pg)
            forced_c, forced_p = [], []
            for t, cw, pw in cyc:
                net = items.get(t, {})
                p = price_at(t)
                up = [k for k, v in net.items() if k > p and v > 0]
                dn = [k for k, v in net.items() if k < p and v < 0]
                fc = max(up, key=lambda k: net[k]) if up else None
                fp = min(dn, key=lambda k: net[k]) if dn else None
                forced_c.append(fc)
                forced_p.append(fp)
                agree[(sym, "call")][0] += fc == cw
                agree[(sym, "call")][1] += 1
                agree[(sym, "put")][0] += fp == pw
                agree[(sym, "put")][1] += 1
                if cw is not None:
                    wrong_side[(sym, "call")][0] += cw <= p
                    wrong_side[(sym, "call")][1] += 1
                if pw is not None:
                    wrong_side[(sym, "put")][0] += pw >= p
                    wrong_side[(sym, "put")][1] += 1
            used.append(f"{sym} {day} cycle {first[0]:%H:%M:%S} price {entry:g} ATR {atr:.2f} IV {sigma:.3f} (chain {snap:%H:%M:%S}) 1-Z {sigma_pts:.2f} pts")
            day_hi, day_lo = max(prices), min(prices)
            i_first = cyc.index(first)
            for kind, cw_s, pw_s in (("guardada", [x[1] for x in cyc], [x[2] for x in cyc]), ("forzada", forced_c, forced_p)):
                for wall, level in (("call", cw_s[i_first]), ("put", pw_s[i_first])):
                    if level is None or level == entry:
                        continue
                    side = 1 if level > entry else -1
                    exits.append({"sym": sym, "day": day, "kind": kind, "wall": wall, "side": side,
                                  **exit_metrics(times, prices, level, side, entry, sigma_pts, atr, day_hi, day_lo, first[0])})
                ctimes = [x[0] for x in cyc]
                for wall, series in (("call", cw_s), ("put", pw_s)):
                    for a, b, level in runs(ctimes, series):
                        reacts.append({"sym": sym, "day": day, "kind": kind, "wall": wall, "out": reaction(times, prices, level, a, b)})
        print(f"... {sym} done", flush=True)
    s.rollback()

print("\nPRUEBA 3 (b) DEL PREREGISTRO: PAREDES TAL COMO SON vs FORZADAS A CADA LADO — corrida 2026-10-31, solo lectura")
print("=" * 110)
print("""DESVÍOS Y ACLARACIONES (2026-10-31, escritos antes de leer los resultados):
 D1. Las forzadas se rearman desde los items guardados del MISMO ciclo táctico (gamma neta = call + put por strike, la ventana estrecha que
     guarda la vista); "por encima / por debajo" usa el último precio guardado antes del ciclo.
 D2. σ para el exceso en Z: IV ATM del vencimiento más cercano en la primera foto de cadena con IV entre 09:33 y 10:00 (en la prueba 1 solo se
     usaba la de las 09:35 y el 09-28 quedó fuera); T al ciclo de las 09:35.
 D3. Objetivo de salida: las paredes del primer ciclo ≥ 09:35, en la dirección en que están respecto del precio de entrada. Una pared guardada
     del lado "equivocado" (Call Wall por debajo del precio) se evalúa igual, como objetivo hacia abajo; abajo se informa cuántas veces pasa.
 D4. Reacción: cada tramo en que la pared queda en el mismo strike; se juzga el primer toque dentro del tramo que llega desde afuera
     (si el tramo empieza con el precio ya en el nivel, no se juzga). Ventana de 30 min aunque pase el final del tramo.
 D5. Sin base al azar en esta prueba: el preregistro compara (1) contra (2).""")
print("\nDÍAS EXCLUIDOS:")
for line in excluded or ["(ninguno)"]:
    print("  " + line)
print("\nSESIONES USADAS")
for line in used:
    print("  " + line)

print("\nCOINCIDENCIA guardada = forzada (% de ciclos) y pared guardada del lado equivocado del precio (% de ciclos)")
for sym in SYMBOLS:
    ca, cn = agree[(sym, "call")]
    pa, pn = agree[(sym, "put")]
    wc, wcn = wrong_side[(sym, "call")]
    wp, wpn = wrong_side[(sym, "put")]
    print(f"   {sym}: Call coincide {100 * ca / max(cn, 1):5.1f} %  Put coincide {100 * pa / max(pn, 1):5.1f} %  |  Call Wall ≤ precio {100 * wc / max(wcn, 1):5.1f} %  "
          f"Put Wall ≥ precio {100 * wp / max(wpn, 1):5.1f} %")


def med(xs):
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else float("nan")


print("\nOBJETIVO DE SALIDA (paredes del ciclo de las 09:35): acierto, minutos (mediana), peor en contra (mediana, pts), exceso (mediana, Z), máx/mín cerca")
for sym in list(SYMBOLS) + ["TODOS"]:
    for wall in ("call", "put"):
        line = []
        for kind in ("guardada", "forzada"):
            xs = [e for e in exits if (sym == "TODOS" or e["sym"] == sym) and e["wall"] == wall and e["kind"] == kind]
            if not xs:
                line.append(f"{kind}: n=0")
                continue
            line.append(f"{kind}: n={len(xs):3d} acierto {100 * sum(e['touch'] for e in xs) / len(xs):5.1f} % min {med([e['minutes'] for e in xs]):6.1f} "
                        f"contra {med([e['adverse'] for e in xs]):7.2f} exceso {med([e['over_z'] for e in xs]):.2f} Z cerca {100 * sum(e['near'] for e in xs) / len(xs):5.1f} %")
        groups = defaultdict(list)
        for d in {e["day"] for e in exits if sym == "TODOS" or e["sym"] == sym}:
            for s_ in ([sym] if sym != "TODOS" else SYMBOLS):
                g = next((e for e in exits if e["sym"] == s_ and e["day"] == d and e["wall"] == wall and e["kind"] == "guardada"), None)
                f = next((e for e in exits if e["sym"] == s_ and e["day"] == d and e["wall"] == wall and e["kind"] == "forzada"), None)
                if g and f:
                    groups[d].append(g["touch"] - f["touch"])
        m, lo, hi = boot_ci(groups)
        diff = "  n/a" if m is None else f"{100 * m:+.1f} pp [{100 * lo:+.1f}, {100 * hi:+.1f}]"
        print(f"   {sym:5} {wall:4} | {line[0]} | {line[1]} | toque guardada − forzada {diff}")

print("\nREACCIÓN EN TODA LA SESIÓN (cada tramo de pared): tramos, tocados, rebote / cruce entre los tocados")
for sym in list(SYMBOLS) + ["TODOS"]:
    for wall in ("call", "put"):
        line = []
        rate = {}
        for kind in ("guardada", "forzada"):
            xs = [r for r in reacts if (sym == "TODOS" or r["sym"] == sym) and r["wall"] == wall and r["kind"] == kind]
            tt = [r for r in xs if r["out"]]
            b = sum(1 for r in tt if r["out"] == "bounce")
            cr = sum(1 for r in tt if r["out"] == "cross")
            line.append(f"{kind}: tramos {len(xs):4d} tocados {len(tt):4d} rebote {100 * b / len(tt) if tt else float('nan'):5.1f} % cruce {100 * cr / len(tt) if tt else float('nan'):5.1f} %")
        groups = defaultdict(list)
        sel = [r for r in reacts if (sym == "TODOS" or r["sym"] == sym) and r["wall"] == wall and r["out"]]
        for key in {(r["sym"], r["day"]) for r in sel}:
            g = [r for r in sel if (r["sym"], r["day"]) == key and r["kind"] == "guardada"]
            f = [r for r in sel if (r["sym"], r["day"]) == key and r["kind"] == "forzada"]
            if g and f:
                groups[key[1]].append(sum(r["out"] == "bounce" for r in g) / len(g) - sum(r["out"] == "bounce" for r in f) / len(f))
        m, lo, hi = boot_ci(groups)
        diff = "  n/a" if m is None else f"{100 * m:+.1f} pp [{100 * lo:+.1f}, {100 * hi:+.1f}]"
        print(f"   {sym:5} {wall:4} | {line[0]} | {line[1]} | rebote guardada − forzada (por día) {diff}")
