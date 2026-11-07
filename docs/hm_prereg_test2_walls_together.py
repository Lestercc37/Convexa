"""READ-ONLY. Preregistered TEST 2 (docs/preregistro_nivel_apertura_2026-10-29.txt, section 2, prueba (a)): "walls together = narrow day".

Condition (measured ONLY up to 10:30): stored tactical Call Wall and Put Wall within <= 2 strike steps during >= 25 % of the cycles 09:30-10:30
(variant: together at the last cycle <= 10:30). Result (measured ONLY after 10:30): range 10:30-16:00 (points and ATR) and the share of the price
readings after 10:30 within 1.5 strike steps of the walls' midpoint (the midpoint of the walls in force at each reading; variant: frozen at 10:30).
Base: days with walls apart (> 2 steps) of the same symbol. Bootstrap (1,000) of the difference of means, resampling days inside each group."""
import random
import sys
from bisect import bisect_right
from collections import Counter
from datetime import datetime, time
from zoneinfo import ZoneInfo

sys.path.insert(0, "V:/Convexa/repo")
sys.stdout.reconfigure(encoding="utf-8")
from sqlalchemy import text

from backend.core.container import build_container

ET = ZoneInfo("America/New_York")
SYMBOLS = ("SPX", "NDX", "SPY", "QQQ", "IWM")
GAP_MIN = 10
BOOT = 1000
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


def strike_step(strikes, price, atr):
    """Most common gap between neighbouring stored strikes within +-0.5 ATR of the price (NDX and IWM mix steps; the near-money one counts)."""
    ks = sorted(k for k in strikes if abs(k - price) <= 0.5 * atr) or sorted(strikes)
    gaps = [round(b - a, 4) for a, b in zip(ks, ks[1:]) if b > a]
    return Counter(gaps).most_common(1)[0][0] if gaps else None


def boot_diff(a, b):
    if not a or not b:
        return None, None, None
    d = sum(a) / len(a) - sum(b) / len(b)
    reps = []
    for _ in range(BOOT):
        ra = [RNG.choice(a) for _ in a]
        rb = [RNG.choice(b) for _ in b]
        reps.append(sum(ra) / len(ra) - sum(rb) / len(rb))
    reps.sort()
    return d, reps[int(0.025 * BOOT)], reps[int(0.975 * BOOT) - 1]


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


c = build_container()
days = []
excluded = []
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
            o, t1030, ten, cl = (datetime.combine(day, time(*hm), ET) for hm in ((9, 30), (10, 30), (10, 0), (16, 0)))
            cyc = [(t.astimezone(ET), None if cw is None else float(cw), None if pw is None else float(pw), t) for t, cw, pw in s.execute(text("""
                select time, call_wall, put_wall from gamma_aggregates where underlying_id = :u and view = 'tactical' and time between :a and :b order by time"""),
                {"u": u, "a": o, "b": cl})]
            rows = [(t.astimezone(ET), float(p)) for t, p in s.execute(text(
                "select time, price from market_snapshots where underlying_id = :u and time between :a and :b order by time"), {"u": u, "a": o, "b": cl})]
            if not cyc or not rows:
                excluded.append(f"{sym} {day}: no cycles or prices")
                continue
            gp = max_gap_minutes([r[0] for r in rows], o, ten)
            gc = max_gap_minutes([x[0] for x in cyc], o, ten)
            if gp > GAP_MIN or gc > GAP_MIN:
                excluded.append(f"{sym} {day}: max gap prices {gp:.1f} min, cycles {gc:.1f} min (09:30-10:00)")
                continue
            atr = atr_for(bars, day)
            if atr is None:
                excluded.append(f"{sym} {day}: fewer than 15 daily bars")
                continue
            first = next((x for x in cyc if x[0] >= datetime.combine(day, time(9, 35), ET)), cyc[0])
            strikes = [float(k) for (k,) in s.execute(text(
                "select strike from gamma_aggregate_items where underlying_id = :u and view = 'tactical' and time = :t"), {"u": u, "t": first[3]})]
            price_at = rows[bisect_right([r[0] for r in rows], first[0]) - 1][1] if rows[0][0] <= first[0] else rows[0][1]
            step = strike_step(strikes, price_at, atr)
            if not step:
                excluded.append(f"{sym} {day}: no strike step")
                continue
            morning = [x for x in cyc if x[0] <= t1030]
            together = lambda x: x[1] is not None and x[2] is not None and abs(x[1] - x[2]) <= 2 * step + 1e-9
            frac = sum(1 for x in morning if together(x)) / len(morning) if morning else 0.0
            at1030 = together(morning[-1]) if morning else False
            after = [r for r in rows if r[0] >= t1030]
            before = [r for r in rows if r[0] <= t1030]
            if len(after) < 100 or not before:
                excluded.append(f"{sym} {day}: session does not reach past 10:30")
                continue
            ctimes = [x[0] for x in cyc]
            near = n = 0
            last = morning[-1] if morning else None
            frozen_mid = (last[1] + last[2]) / 2 if last and last[1] is not None and last[2] is not None else None
            near_frozen = 0
            for t, p in after:
                i = bisect_right(ctimes, t) - 1
                if i < 0:
                    continue
                cw, pw = cyc[i][1], cyc[i][2]
                if cw is not None and pw is not None:
                    n += 1
                    near += abs(p - (cw + pw) / 2) <= 1.5 * step
                if frozen_mid is not None:
                    near_frozen += abs(p - frozen_mid) <= 1.5 * step
            pa = [p for _, p in after]
            pb = [p for _, p in before]
            days.append({"sym": sym, "day": day, "step": step, "atr": atr, "frac": frac, "at1030": at1030, "together": frac >= 0.25,
                         "range_after": max(pa) - min(pa), "range_after_atr": (max(pa) - min(pa)) / atr,
                         "range_before_atr": (max(pb) - min(pb)) / atr, "near_mid": near / n if n else None,
                         "near_frozen": near_frozen / len(after) if frozen_mid is not None else None,
                         "null_share": sum(1 for x in cyc if x[1] is None or x[2] is None) / len(cyc)})
    s.rollback()

print("PRUEBA 2 (a) DEL PREREGISTRO: \"PAREDES JUNTAS = DÍA ESTRECHO\" — corrida 2026-10-31, solo lectura")
print("=" * 110)
print("""DESVÍOS Y ACLARACIONES (2026-10-31, escritos antes de leer los resultados):
 D1. Escalón de strike: el salto más común entre strikes guardados vecinos a ±0,5 ATR del precio en el ciclo de las 09:35 (NDX e IWM mezclan
     escalones; cuenta el cercano al dinero).
 D2. "≥ 25 % de la sesión": se mide sobre los ciclos tácticos de 09:30 a 10:30 (el preregistro pide medir la condición solo hasta las 10:30).
     Variante: juntas en el último ciclo antes de las 10:30 (la "hora fija"). Ciclos con una pared nula cuentan como "no juntas".
 D3. Punto medio: el de las paredes vigentes en cada lectura de precio después de las 10:30 (variante: congelado a las 10:30).
     % de tiempo = % de lecturas de precio (llegan ~1 por segundo, parejas en el tiempo).
 D4. El rango se informa en puntos y en ATR; las comparaciones entre días usan ATR (cada día tiene distinta volatilidad).
 D5. Los días de mercado se toman todos (también 09-25, primer día táctico); exclusión solo por huecos > 10 min de 09:30 a 10:00.""")
print("\nDÍAS EXCLUIDOS:")
for line in excluded or ["(ninguno)"]:
    print("  " + line)
print("\nPOR DÍA: sím fecha | escalón | % ciclos juntas 09:30-10:30 | juntas 10:30 | rango 09:30-10:30 (ATR) | rango después 10:30 (pts / ATR) | % cerca del medio (vigente / congelado) | % ciclos con pared nula")
for d in days:
    print(f"  {d['sym']} {d['day']} | {d['step']:g} | {100 * d['frac']:5.1f} % | {'sí' if d['at1030'] else 'no'} | {d['range_before_atr']:.2f} | "
          f"{d['range_after']:.2f} / {d['range_after_atr']:.2f} | {100 * d['near_mid'] if d['near_mid'] is not None else float('nan'):5.1f} / "
          f"{100 * d['near_frozen'] if d['near_frozen'] is not None else float('nan'):5.1f} | {100 * d['null_share']:4.1f} %")


def compare(label, key):
    print(f"\n{label}")
    print(f"   {'sím':5} {'juntas n':>8} {'separadas n':>11} | {'rango después 10:30 (ATR) juntas / separadas, dif [IC 95 %]':58} | % cerca del medio juntas / separadas, dif [IC 95 %] | rango antes (ATR)")
    for sym in list(SYMBOLS) + ["TODOS"]:
        sel = [d for d in days if sym == "TODOS" or d["sym"] == sym]
        a = [d for d in sel if d[key]]
        b = [d for d in sel if not d[key]]
        r = boot_diff([d["range_after_atr"] for d in a], [d["range_after_atr"] for d in b])
        na = [d["near_mid"] for d in a if d["near_mid"] is not None]
        nb = [d["near_mid"] for d in b if d["near_mid"] is not None]
        m = boot_diff(na, nb)
        fmt = lambda x: "  n/a" if x[0] is None else f"{x[0]:+.2f} [{x[1]:+.2f}, {x[2]:+.2f}]"
        fmtp = lambda x: "  n/a" if x[0] is None else f"{100 * x[0]:+.1f} pp [{100 * x[1]:+.1f}, {100 * x[2]:+.1f}]"
        print(f"   {sym:5} {len(a):8d} {len(b):11d} | {mean([d['range_after_atr'] for d in a]):5.2f} / {mean([d['range_after_atr'] for d in b]):5.2f}  {fmt(r):34} | "
              f"{100 * mean(na):5.1f} / {100 * mean(nb):5.1f}  {fmtp(m):30} | {mean([d['range_before_atr'] for d in a]):.2f} / {mean([d['range_before_atr'] for d in b]):.2f}")


compare("CONDICIÓN PRINCIPAL: paredes a ≤ 2 escalones durante ≥ 25 % de los ciclos 09:30-10:30", "together")
compare("VARIANTE HORA FIJA: paredes a ≤ 2 escalones en el último ciclo antes de las 10:30", "at1030")
print("\nLectura: se espera rango después de las 10:30 MENOR y % cerca del medio MAYOR en los días 'juntas'. 'Rango antes' muestra si esos días ya")
print("venían quietos (causalidad inversa): por eso el resultado se mide solo después de las 10:30.")
print("Primer vistazo del 10-27 (no cuenta): SPY 5 días, rango medio 4,47 vs 8,35; QQQ no lo confirmó.")
