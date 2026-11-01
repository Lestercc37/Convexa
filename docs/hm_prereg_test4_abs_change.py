"""READ-ONLY. Preregistered TEST 4 (docs/preregistro_nivel_apertura_2026-10-29.txt, section 4, prueba (c)): "ABS change = price goes there".

Events: every cycle where the stored ABS (gamma_aggregates.absolute_gamma_strike) moves to a different strike, per view (tactical, 0dte,
0dte_volume) and direction (new ABS above / below the price), counted ONLY when the new ABS is >= 1 strike step from the price at that moment.
Metric: touched (within 0.05 %) within 15 and within 30 minutes. Base: levels at the SAME Z distance and on the same side placed at random
cycle times of the same symbol and view (rounded to the strike grid, >= 1 step away), same 15 / 30 min metric. Bootstrap (1,000) by day."""
import math
import random
import sys
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, "V:/Convexa/repo")
sys.stdout.reconfigure(encoding="utf-8")
from sqlalchemy import text

from backend.adapters.providers.thetadata.provider import _time_to_expiration_years
from backend.core.container import build_container

ET = ZoneInfo("America/New_York")
SYMBOLS = ("SPX", "NDX", "SPY", "QQQ", "IWM")
VIEWS = ("tactical", "0dte", "0dte_volume")
TOUCH, GAP_MIN, BOOT, BASE_DRAWS = 0.0005, 10, 1000, 40
LAST_EVENT = time(15, 30)
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
    ks = sorted(k for k in strikes if abs(k - price) <= 0.5 * atr) or sorted(strikes)
    gaps = [round(b - a, 4) for a, b in zip(ks, ks[1:]) if b > a]
    return Counter(gaps).most_common(1)[0][0] if gaps else None


def day_sigma(s, u, day, entry):
    """ATM IV (call/put average, IV > 0) and the nearest expiration, from the first chain snapshot in 09:33-10:00 that has one."""
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
            return sum(ivs) / len(ivs), exps[0]
    return None, None


def touched_within(times, prices, t, level, side, minutes):
    i0, i1 = bisect_right(times, t), bisect_right(times, t + timedelta(minutes=minutes))
    if i1 <= i0:
        return False
    if side > 0:
        return max(prices[i0:i1]) >= level * (1 - TOUCH)
    return min(prices[i0:i1]) <= level * (1 + TOUCH)


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
events = []
excluded = []
used = []
skipped = Counter()
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
        sessions = {}   # day -> (times, prices, step, sigma, expiration)
        cycles = {}     # (view, day) -> [(t, abs)]
        for day in dates:
            o, ten, cl = (datetime.combine(day, time(*hm), ET) for hm in ((9, 30), (10, 0), (16, 0)))
            rows = [(t.astimezone(ET), float(p)) for t, p in s.execute(text(
                "select time, price from market_snapshots where underlying_id = :u and time between :a and :b order by time"), {"u": u, "a": o, "b": cl})]
            tac = [t.astimezone(ET) for (t,) in s.execute(text(
                "select time from gamma_aggregates where underlying_id = :u and view = 'tactical' and time between :a and :b"), {"u": u, "a": o, "b": ten})]
            if not rows or not tac:
                excluded.append(f"{sym} {day}: no prices or cycles")
                continue
            gp, gc = max_gap_minutes([r[0] for r in rows], o, ten), max_gap_minutes(tac, o, ten)
            if gp > GAP_MIN or gc > GAP_MIN:
                excluded.append(f"{sym} {day}: max gap prices {gp:.1f} min, cycles {gc:.1f} min (09:30-10:00)")
                continue
            atr = atr_for(bars, day)
            times, prices = [r[0] for r in rows], [r[1] for r in rows]
            first_t = min(tac)
            strikes = [float(k) for (k,) in s.execute(text("""select strike from gamma_aggregate_items where underlying_id = :u and view = 'tactical'
                and time = (select min(time) from gamma_aggregates where underlying_id = :u and view = 'tactical' and time >= :t)"""),
                {"u": u, "t": datetime.combine(day, time(9, 35), ET)})]
            entry = prices[max(bisect_right(times, datetime.combine(day, time(9, 35), ET)) - 1, 0)]
            step = strike_step(strikes, entry, atr) if atr and strikes else None
            sigma, exp = day_sigma(s, u, day, entry)
            if not step or not sigma:
                excluded.append(f"{sym} {day}: no strike step or no ATM IV 09:33-10:00")
                continue
            sessions[day] = (times, prices, step, sigma, exp)
            used.append(f"{sym} {day} step {step:g} IV {sigma:.3f}")
            for view in VIEWS:
                cyc = [(t.astimezone(ET), None if a is None else float(a)) for t, a in s.execute(text("""
                    select time, absolute_gamma_strike from gamma_aggregates where underlying_id = :u and view = :v and time between :a and :b order by time"""),
                    {"u": u, "v": view, "a": o, "b": cl})]
                if cyc:
                    cycles[(view, day)] = cyc

        def sigma_pts(day, t, price):
            _, _, _, sigma, exp = sessions[day]
            return price * sigma * math.sqrt(float(_time_to_expiration_years(exp, t)))

        for view in VIEWS:
            pool = [(day, t) for (v, day), cyc in cycles.items() if v == view for t, _ in cyc if time(9, 35) <= t.time() <= LAST_EVENT]
            for (v, day), cyc in sorted(cycles.items()):
                if v != view:
                    continue
                times, prices, step, _, _ = sessions[day]
                for (t0, a0), (t1, a1) in zip(cyc, cyc[1:]):
                    if a0 is None or a1 is None or a1 == a0:
                        continue
                    if t1.time() > LAST_EVENT or t1.time() < time(9, 31):
                        skipped["fuera de 09:31-15:30"] += 1
                        continue
                    p = prices[max(bisect_right(times, t1) - 1, 0)]
                    if abs(a1 - p) < step - 1e-9:
                        skipped["a < 1 escalón del precio"] += 1
                        continue
                    side = 1 if a1 > p else -1
                    z = abs(a1 - p) / sigma_pts(day, t1, p)
                    ev = {"sym": sym, "view": view, "day": day, "t": t1, "side": side, "z": z, "dist_steps": abs(a1 - p) / step,
                          "hit15": touched_within(times, prices, t1, a1, side, 15), "hit30": touched_within(times, prices, t1, a1, side, 30),
                          "back5": any(a == a0 for tt, a in cyc if t1 < tt <= t1 + timedelta(minutes=5))}
                    # base: same Z, same side, random cycle times of the same symbol and view
                    b15 = b30 = 0
                    for _ in range(BASE_DRAWS):
                        d2, t2 = RNG.choice(pool)
                        tms, prs, st2, _, _ = sessions[d2]
                        p2 = prs[max(bisect_right(tms, t2) - 1, 0)]
                        raw = p2 + side * z * sigma_pts(d2, t2, p2)
                        k = round(raw / st2) * st2
                        if (k - p2) * side < st2 - 1e-9:
                            k = (math.ceil((p2 + 1e-9) / st2) * st2 if side > 0 else math.floor((p2 - 1e-9) / st2) * st2)
                            if abs(k - p2) < st2 - 1e-9:
                                k += side * st2
                        b15 += touched_within(tms, prs, t2, k, side, 15)
                        b30 += touched_within(tms, prs, t2, k, side, 30)
                    ev["base15"], ev["base30"] = b15 / BASE_DRAWS, b30 / BASE_DRAWS
                    events.append(ev)
        print(f"... {sym} done ({sum(1 for e in events if e['sym'] == sym)} events)", flush=True)
    s.rollback()

print("\nPRUEBA 4 (c) DEL PREREGISTRO: \"CAMBIO DE ABS = EL PRECIO VA AHÍ\" — corrida 2026-10-31, solo lectura")
print("=" * 110)
print(f"""DESVÍOS Y ACLARACIONES (2026-10-31, escritos antes de leer los resultados):
 D1. Evento = el ABS guardado del ciclo es un strike distinto del ciclo anterior (cualquier cambio, también volver a uno anterior); se informa
     aparte cuántos vuelven al strike previo en ≤ 5 min (parpadeo).
 D2. Solo eventos entre 09:31 y 15:30, para que la ventana de 30 min quepa en la sesión (el preregistro no lo dice).
 D3. Escalón de strike: el salto más común entre strikes tácticos guardados a ±0,5 ATR del precio a las 09:35 (NDX/IWM mezclan escalones).
     Mismo escalón para las vistas 0dte y 0dte_volume.
 D4. Z = |ABS − precio| / (precio · σ · √T): σ = IV ATM del vencimiento más cercano en la primera foto de cadena con IV de 09:33-10:00;
     T del vencimiento a la hora del evento (_time_to_expiration_years, con su piso de 1 hora).
 D5. Base "strikes al azar a la misma Z": {BASE_DRAWS} sorteos por evento de una hora de ciclo al azar (09:35-15:30) del mismo símbolo y vista,
     nivel a la misma Z y del mismo lado, redondeado al escalón y a ≥ 1 escalón del precio; se usa la tasa de toque media de los sorteos.
 D6. Vistas 0dte y 0dte_volume: solo existen del 10-26 al 10-30 (5 días); se informan igual, con pocos días.""")
print("\nDÍAS EXCLUIDOS:")
for line in excluded or ["(ninguno)"]:
    print("  " + line)
print("\nCAMBIOS DESCARTADOS: " + ", ".join(f"{k}: {v}" for k, v in skipped.items()))
print("\nSESIONES USADAS: " + "; ".join(used))


def table(sel, label):
    if not sel:
        print(f"   {label:34} n=   0")
        return
    g15, g30 = defaultdict(list), defaultdict(list)
    for e in sel:
        g15[e["day"]].append(e["hit15"] - e["base15"])
        g30[e["day"]].append(e["hit30"] - e["base30"])
    m15, l15, h15 = boot_ci(g15)
    m30, l30, h30 = boot_ci(g30)
    n = len(sel)
    print(f"   {label:34} n={n:4d} días={len({e['day'] for e in sel}):2d} |Z| med {sorted(e['z'] for e in sel)[n // 2]:.2f} "
          f"parpadeo {100 * sum(e['back5'] for e in sel) / n:4.0f} % | 15 min: {100 * sum(e['hit15'] for e in sel) / n:5.1f} % vs base "
          f"{100 * sum(e['base15'] for e in sel) / n:5.1f} % dif {100 * m15:+5.1f} [{100 * l15:+5.1f}, {100 * h15:+5.1f}] | 30 min: "
          f"{100 * sum(e['hit30'] for e in sel) / n:5.1f} % vs {100 * sum(e['base30'] for e in sel) / n:5.1f} % dif {100 * m30:+5.1f} [{100 * l30:+5.1f}, {100 * h30:+5.1f}]")


for view in VIEWS:
    print(f"\nVISTA {view}: tocado en 15 / 30 min vs base a la misma Z (diferencia en pp, IC 95 % bootstrap por día)")
    for sym in list(SYMBOLS) + ["TODOS"]:
        for side, sname in ((1, "nuevo ABS arriba"), (-1, "nuevo ABS abajo")):
            table([e for e in events if e["view"] == view and (sym == "TODOS" or e["sym"] == sym) and e["side"] == side], f"{sym} {sname}")
        table([e for e in events if e["view"] == view and (sym == "TODOS" or e["sym"] == sym)], f"{sym} ambos")

print("\nSIN PARPADEO (el ABS no vuelve al strike anterior en ≤ 5 min), todas las vistas por separado, todos los símbolos:")
for view in VIEWS:
    table([e for e in events if e["view"] == view and not e["back5"]], f"{view} sin parpadeo")
print("\nPrimer vistazo del 10-27 (no cuenta): SPX táctico, cambios a 7700 arriba del precio → tocados 3 de 4; a 7650 abajo → 0 de 5.")
