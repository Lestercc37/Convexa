"""READ-ONLY. Preregistered TEST 5 (docs/preregistro_nivel_apertura_2026-10-29.txt, section 5, prueba (d)), Lester 2026-10-30:
"the Gamma Flip often comes closer to the price and right after it changes sign and the price goes that way".

Per symbol and stored tactical session (cycles ~1/min, gamma_aggregates view 'tactical'):
- distance d(t) = |price - Flip| / (price * sigma * sqrt(T)) in Z (sigma = the day's ATM IV, T to the nearest expiration at t).
- APPROACH at cycle t: d dropped >= 50 % against the cycle ~15 min earlier (13-17 min) and d(t) < 0.3.
- SIGN CHANGE: the wide-window regime (gamma_aggregates.net_gamma, the dashboard's regime) has the opposite sign to the previous cycle.
- EVENT: a sign change within 10 min after an approach (one event per sign change). Prediction: new regime negative -> price falls,
  positive -> rises. Result: direction of the price 15 and 30 min after the sign change; hit %, mean move in favour.
- Baselines: (1) the same minutes of the day on the other sessions of the symbol, away from events, with the same predicted direction;
  (2) sign changes WITHOUT an approach in the 10 min before.
- Acceptance: hits >= 60 % with >= 30 events in total and a 95 % binomial interval (Clopper-Pearson) that does not include 50 %.
  Fewer than 30 events: "sin conclusión".
- Also: how many sign changes were only the price crossing a still Flip (Flip moved < 1 strike step since the approach started and the
  price changed side of it)."""
import math
import sys
from bisect import bisect_right
from collections import Counter
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, "V:/Convexa/repo")
sys.stdout.reconfigure(encoding="utf-8")
from sqlalchemy import text

from backend.adapters.providers.thetadata.provider import _time_to_expiration_years
from backend.core.container import build_container

ET = ZoneInfo("America/New_York")
SYMBOLS = ("SPX", "NDX", "SPY", "QQQ", "IWM")
GAP_MIN = 10
APPROACH_LOOKBACK = (13, 17)   # minutes: the "15 min earlier" cycle
APPROACH_DROP = 0.50
APPROACH_END_Z = 0.30
SIGN_WITHIN = 10               # minutes after the approach
HORIZONS = (15, 30)
LAST_EVENT = time(15, 30)
BASE_SPACING = 30              # minutes away from any event for baseline (1)


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


def binom_cdf(k, n, p):
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(0, k + 1))


def clopper_pearson(k, n, alpha=0.05):
    """Exact binomial 95 % interval by bisection (no scipy on the server)."""
    if n == 0:
        return None, None

    def solve(f):
        lo, hi = 0.0, 1.0
        for _ in range(60):
            mid = (lo + hi) / 2
            if f(mid):
                hi = mid
            else:
                lo = mid
        return (lo + hi) / 2

    lower = 0.0 if k == 0 else solve(lambda p: 1 - binom_cdf(k - 1, n, p) >= alpha / 2)
    upper = 1.0 if k == n else solve(lambda p: binom_cdf(k, n, p) <= alpha / 2)
    return lower, upper


c = build_container()
sessions = {}       # (sym, day) -> dict
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
            o, t935, ten, cl = (datetime.combine(day, time(*hm), ET) for hm in ((9, 30), (9, 35), (10, 0), (16, 0)))
            rows = [(t.astimezone(ET), float(p)) for t, p in s.execute(text(
                "select time, price from market_snapshots where underlying_id = :u and time between :a and :b order by time"), {"u": u, "a": o, "b": cl})]
            cyc = [(t.astimezone(ET), None if f is None else float(f), None if n is None else float(n)) for t, f, n in s.execute(text("""
                select time, gamma_flip, net_gamma from gamma_aggregates where underlying_id = :u and view = 'tactical' and time between :a and :b order by time"""),
                {"u": u, "a": o, "b": cl})]
            if not rows or not cyc:
                excluded.append(f"{sym} {day}: no prices or cycles")
                continue
            gp, gc = max_gap_minutes([r[0] for r in rows], o, ten), max_gap_minutes([x[0] for x in cyc], o, ten)
            if gp > GAP_MIN or gc > GAP_MIN:
                excluded.append(f"{sym} {day}: max gap prices {gp:.1f} min, cycles {gc:.1f} min (09:30-10:00)")
                continue
            times, prices = [r[0] for r in rows], [r[1] for r in rows]
            entry = prices[max(bisect_right(times, t935) - 1, 0)]
            atr = atr_for(bars, day)
            first = next((x[0] for x in cyc if x[0] >= t935), None)
            strikes = [float(k) for (k,) in s.execute(text(
                "select strike from gamma_aggregate_items where underlying_id = :u and view = 'tactical' and time = :t"),
                {"u": u, "t": first})] if first else []
            step = strike_step(strikes, entry, atr) if atr and strikes else None
            sigma, exp = day_sigma(s, u, day, entry)
            if not step or not sigma:
                excluded.append(f"{sym} {day}: no strike step or no ATM IV 09:33-10:00")
                continue
            sessions[(sym, day)] = {"times": times, "prices": prices, "cyc": cyc, "step": step, "sigma": sigma, "exp": exp}
        print(f"... {sym} loaded", flush=True)
    s.rollback()


def price_at(sess, t):
    i = bisect_right(sess["times"], t) - 1
    return sess["prices"][max(i, 0)]


def move(sess, t, minutes):
    end = t + timedelta(minutes=minutes)
    if end > sess["times"][-1]:
        return None
    return price_at(sess, end) - price_at(sess, t)


events, plain_changes = [], []
for (sym, day), sess in sessions.items():
    cyc = sess["cyc"]
    ctimes = [x[0] for x in cyc]
    zpts = lambda t, p: p * sess["sigma"] * math.sqrt(float(_time_to_expiration_years(sess["exp"], t)))
    dist = []
    for t, flip, _ in cyc:
        if flip is None:
            dist.append(None)
            continue
        p = price_at(sess, t)
        dist.append(abs(p - flip) / zpts(t, p))
    approaches = []   # (index, index of the ~15-min-earlier cycle)
    for i, (t, _, _) in enumerate(cyc):
        if dist[i] is None or dist[i] >= APPROACH_END_Z:
            continue
        lo, hi = t - timedelta(minutes=APPROACH_LOOKBACK[1]), t - timedelta(minutes=APPROACH_LOOKBACK[0])
        cands = [j for j in range(max(0, i - 40), i) if lo <= ctimes[j] <= hi and dist[j] is not None]
        if not cands:
            continue
        j = min(cands, key=lambda j: abs((t - ctimes[j]).total_seconds() - 900))
        if dist[j] > 0 and dist[i] <= (1 - APPROACH_DROP) * dist[j]:
            approaches.append((i, j))
    changes = [i for i in range(1, len(cyc)) if cyc[i][2] is not None and cyc[i - 1][2] is not None
               and cyc[i][2] != 0 and cyc[i - 1][2] != 0 and (cyc[i][2] > 0) != (cyc[i - 1][2] > 0)]
    event_times = []
    for i in changes:
        tc = ctimes[i]
        if tc.time() > LAST_EVENT or tc.time() < time(9, 45):
            continue
        sign = 1 if cyc[i][2] > 0 else -1
        prior = [(a, j) for a, j in approaches if ctimes[a] <= tc and tc - ctimes[a] <= timedelta(minutes=SIGN_WITHIN)]
        rec = {"sym": sym, "day": day, "t": tc, "sign": sign, "z": zpts(tc, price_at(sess, tc))}
        for h in HORIZONS:
            rec[f"m{h}"] = move(sess, tc, h)
        if prior:
            a, j = prior[-1]
            f0, fc = cyc[j][1], cyc[i][1]
            p0, pc = price_at(sess, ctimes[j]), price_at(sess, tc)
            rec["still_flip_cross"] = (f0 is not None and fc is not None and abs(fc - f0) < sess["step"] and (p0 - f0) * (pc - fc) < 0)
            rec["approach_at"] = ctimes[a]
            events.append(rec)
            event_times.append(tc)
        else:
            plain_changes.append(rec)
    sess["event_times"] = event_times


def score(recs, h):
    usable = [r for r in recs if r[f"m{h}"] is not None]
    hits = sum(1 for r in usable if r[f"m{h}"] * r["sign"] > 0)
    favour = [r[f"m{h}"] * r["sign"] / r["z"] for r in usable]
    return len(usable), hits, (sum(favour) / len(favour) if favour else float("nan"))


def baseline_same_minutes(recs, h):
    """Each event's minute of the day on the other sessions of the same symbol, >= 30 min away from any event there, same predicted sign."""
    n = hits = 0
    for r in recs:
        mod = r["t"].hour * 60 + r["t"].minute
        for (sym, day), sess in sessions.items():
            if sym != r["sym"] or day == r["day"]:
                continue
            target = datetime.combine(day, time(mod // 60, mod % 60), ET)
            if any(abs((target - te).total_seconds()) < BASE_SPACING * 60 for te in sess["event_times"]):
                continue
            m = move(sess, target, h)
            if m is None:
                continue
            n += 1
            hits += m * r["sign"] > 0
    return n, hits


print("\nPRUEBA 5 (d) DEL PREREGISTRO: EL FLIP SE ACERCA Y, AL CAMBIAR DE SIGNO, EL PRECIO VA HACIA AHÍ — corrida 2026-11-01, solo lectura")
print("=" * 110)
print(f"""DESVÍOS Y ACLARACIONES (2026-11-01, escritos antes de leer los resultados):
 D1. "15 minutos antes" = el ciclo táctico más cercano a 15 min antes, entre 13 y 17 min (los ciclos llegan ~1 por minuto, no exacto).
 D2. Régimen = gamma_aggregates.net_gamma de la vista táctica (la ventana amplia, la misma del badge de régimen). Cambio de signo = signo
     distinto al ciclo anterior (ciclos con 0 o nulo no cuentan).
 D3. Z = |precio − Flip| / (precio · σ · √T): σ = IV ATM del vencimiento más cercano en la primera foto de cadena con IV de 09:33-10:00;
     T del vencimiento a la hora del ciclo (con el piso de 1 hora de ThetaData).
 D4. Solo cambios de signo entre 09:45 y 15:30: antes de 09:45 no hay 15 min de historia para el acercamiento; después de 15:30 no cabe la
     ventana de 30 min. Un evento por cambio de signo (si hubo varios acercamientos antes, cuenta una vez).
 D5. Acierto = el precio se movió en la dirección predicha (variación 0 = no acierto). "Movimiento a favor" en Z del momento del cambio.
 D6. Intervalo binomial exacto (Clopper-Pearson, 95 %). El criterio se aplica por separado a 15 y a 30 min, con todos los símbolos juntos
     (el preregistro dice "al menos 30 eventos en total"); por símbolo se informa como detalle.
 D7. Base (1): el mismo minuto del día en las OTRAS sesiones del símbolo, a ≥ {BASE_SPACING} min de cualquier evento de esa sesión, con la
     misma dirección predicha. Base (2): cambios de signo sin acercamiento en los 10 min previos.
 D8. "Flip quieto cruzado por el precio": el Flip se movió menos de 1 escalón de strike entre el inicio del acercamiento y el cambio de
     signo, y el precio quedó del otro lado del Flip.""")
print("\nDÍAS EXCLUIDOS:")
for line in excluded or ["(ninguno)"]:
    print("  " + line)
print(f"\nSESIONES USADAS: {len(sessions)} ({', '.join(f'{sym} {sum(1 for k in sessions if k[0] == sym)}' for sym in SYMBOLS)})")

print("\nEVENTOS (acercamiento + cambio de signo en ≤ 10 min):")
for e in sorted(events, key=lambda e: (e["sym"], e["t"])):
    fmt = lambda m: "  n/a" if m is None else f"{m:+.2f}"
    print(f"  {e['sym']} {e['t']:%Y-%m-%d %H:%M} acercamiento {e['approach_at']:%H:%M} régimen → {'+' if e['sign'] > 0 else '−'} | "
          f"15 min {fmt(e['m15'])} 30 min {fmt(e['m30'])} pts | flip quieto cruzado: {'sí' if e['still_flip_cross'] else 'no'}")

print("\nRESULTADO (acierto = el precio va en la dirección del nuevo régimen)")
for h in HORIZONS:
    print(f"\n  Horizonte {h} min")
    for sym in list(SYMBOLS) + ["TODOS"]:
        ev = [e for e in events if sym == "TODOS" or e["sym"] == sym]
        pc = [e for e in plain_changes if sym == "TODOS" or e["sym"] == sym]
        n, k, fav = score(ev, h)
        lo, hi = clopper_pearson(k, n)
        bn, bk = baseline_same_minutes(ev, h)
        pn, pk, pfav = score(pc, h)
        rate = f"{100 * k / n:5.1f} % IC [{100 * lo:5.1f}, {100 * hi:5.1f}]" if n else "  n/a"
        print(f"   {sym:5} eventos {n:3d} aciertos {k:3d} = {rate} a favor {fav:+.3f} Z | base (1) mismos minutos {100 * bk / bn if bn else float('nan'):5.1f} % "
              f"(n={bn}) | base (2) cambios sin acercamiento {100 * pk / pn if pn else float('nan'):5.1f} % (n={pn}) a favor {pfav:+.3f} Z")
    n, k, _ = score(events, h)
    lo, hi = clopper_pearson(k, n)
    if n < 30:
        verdict = f"SIN CONCLUSIÓN: {n} eventos (< 30); se repite con más sesiones"
    elif k / n >= 0.60 and lo > 0.50:
        verdict = f"SE ACEPTA: {100 * k / n:.1f} % ≥ 60 % con IC [{100 * lo:.1f}, {100 * hi:.1f}] sobre 50 %"
    else:
        verdict = f"NO SE ACEPTA: {100 * k / n:.1f} % con IC [{100 * lo:.1f}, {100 * hi:.1f}] (pide ≥ 60 % y el IC sin el 50 %)"
    print(f"  => {h} min: {verdict}")

still = sum(1 for e in events if e["still_flip_cross"])
print(f"\nCAMBIOS DE SIGNO QUE FUERON SOLO EL PRECIO CRUZANDO UN FLIP QUIETO: {still} de {len(events)} eventos")
for h in HORIZONS:
    for flag, label in ((True, "flip quieto cruzado"), (False, "flip que se movió")):
        n, k, _ = score([e for e in events if e["still_flip_cross"] == flag], h)
        print(f"   {h} min, {label:20}: {k}/{n} aciertos" + (f" = {100 * k / n:.1f} %" if n else ""))
print(f"\nTotal de cambios de signo 09:45-15:30: {len(events) + len(plain_changes)} (con acercamiento {len(events)}, sin acercamiento {len(plain_changes)})")
print("Antecedente: las pruebas de adelanto del Flip del 10-16 (4, 5 y 6) no encontraron adelanto. Solo lectura; nada se vuelve alerta sin otro sí de Lester.")
