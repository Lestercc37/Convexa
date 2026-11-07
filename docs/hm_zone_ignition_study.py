"""READ-ONLY. Pre-registered study "zona que se enciende" (docs/estudio_nivel_que_se_enciende_prereg_2026-11-07.txt, committed 11:42 before any result).
For every session with the 0dte_volume view stored: zones of bright gamma strikes (|gamma| / column p98 >= 0.60, contiguous strikes, +- T = 0.10 % of price)
that light up (no bright strike inside them in the previous 10 minutes) with the price outside them at T .. 0.60 % away; does the price reach them within
30 minutes more often than the mirror zone (same size, same distance, other side)? Day-clustered bootstrap CI. Secondary: 15/60 min, per symbol, time to
arrival, what happens on arrival (turn / break / nothing within 15 min, 2T), above vs below. Prints a report; writes nothing."""
import random
import statistics
import sys
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, "V:/Convexa/repo")
sys.stdout.reconfigure(encoding="utf-8")
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.api.deps import require_admin, require_session, require_session_ws
from backend.core.container import build_container
from backend.core.sessions import SessionPayload
from backend.main import create_app

ET = ZoneInfo("America/New_York")
SYMBOLS = ("SPX", "NDX", "SPY", "QQQ", "IWM")
PRIMARY = ("SPX", "NDX")
FIRST, LAST = date(2026, 9, 14), date(2026, 11, 6)
BRIGHT = 0.60
TOL = 0.0010
MAX_DIST = 0.0060
LOOKBACK_MIN = 10
DEDUPE_MIN = 30
HORIZONS = (15, 30, 60)
REACTION_MIN = 15
MIN_COLUMNS = 100
MODE = sys.argv[1] if len(sys.argv) > 1 else "lit"  # "lit" = pre-registered; "dominant" = exploratory; "nearwhite" = addendum 11:58


def utc(d: date, h: int, m: int) -> datetime:
    return datetime.combine(d, time(h, m), ET).astimezone(timezone.utc)


def p98(values):
    mags = sorted(abs(v) for v in values if v is not None and v != 0)
    return mags[min(len(mags) - 1, int(0.98 * len(mags)))] if mags else 0.0


def zones_of(strikes, values, scale, tol):
    bright = [scale > 0 and v is not None and abs(v) / scale >= BRIGHT for v in values]
    zones, i = [], 0
    while i < len(strikes):
        if bright[i]:
            j = i
            while j + 1 < len(strikes) and bright[j + 1]:
                j += 1
            zones.append((strikes[i] - tol, strikes[j] + tol))
            i = j + 1
        else:
            i += 1
    return zones, [strikes[k] for k in range(len(strikes)) if bright[k]]


def first_touch(bars, minutes, lo, hi):
    for k, m in enumerate(minutes):
        bar = bars.get(m)
        if bar and bar[0] >= lo and bar[1] <= hi:
            return k + 1, m
    return None, None


def reaction(bars, start_minute, lo, hi, from_below, tol, close_utc):
    """'giro' (back out the side it came from by 2T), 'ruptura' (out the other side by 2T), 'nada' within REACTION_MIN minutes, whichever first."""
    m = start_minute
    for _ in range(REACTION_MIN):
        m = m + timedelta(minutes=1)
        if m >= close_utc:
            break
        bar = bars.get(m)
        if not bar:
            continue
        high, low = bar[0], bar[1]
        back = low <= lo - 2 * tol if from_below else high >= hi + 2 * tol
        through = high >= hi + 2 * tol if from_below else low <= lo - 2 * tol
        if back and through:
            return "ambos"
        if back:
            return "giro"
        if through:
            return "ruptura"
    return "nada"


def main():
    app = create_app()
    fake = SessionPayload(user_id=0, username="measure", is_admin=False)
    for dep in (require_session, require_session_ws, require_admin):
        app.dependency_overrides[dep] = lambda: fake
    container = build_container()
    bars_by_symbol: dict[str, dict[datetime, tuple[float, float, float]]] = {}
    with container.storage.session_factory() as session:
        for symbol in SYMBOLS:
            rows = session.execute(text(
                "SELECT date_trunc('minute', m.time) AS minute, max(m.price) AS high, min(m.price) AS low, "
                "(array_agg(m.price ORDER BY m.time DESC))[1] AS close "
                "FROM market_snapshots m JOIN underlyings u ON u.id = m.underlying_id "
                "WHERE u.symbol = :symbol AND m.time >= :since GROUP BY 1"), {"symbol": symbol, "since": utc(FIRST, 0, 0)}).all()
            bars_by_symbol[symbol] = {r.minute.astimezone(timezone.utc): (float(r.high), float(r.low), float(r.close)) for r in rows}

    events = []  # dicts
    sessions = {s: 0 for s in SYMBOLS}
    with TestClient(app) as client:
        d = FIRST
        while d <= LAST:
            if d.weekday() >= 5:
                d += timedelta(days=1)
                continue
            for symbol in SYMBOLS:
                r = client.get(f"/api/v1/gamma/{symbol}/heatmap", params={
                    "view": "0dte_volume", "metric": "gamma", "start": utc(d, 9, 30).isoformat().replace("+00:00", "Z"),
                    "end": utc(d, 16, 0).isoformat().replace("+00:00", "Z"), "bucket_seconds": 60, "source": "cycle"})
                if r.status_code != 200:
                    continue
                body = r.json()
                strikes = [float(s) for s in body.get("strikes") or []]
                cols = []
                for col in body.get("columns") or []:
                    values = col.get("values") or []
                    if not any(v not in (None, 0) for v in values):
                        continue
                    t = datetime.fromisoformat(str(col["as_of"]).replace("Z", "+00:00")).replace(second=0, microsecond=0)
                    cols.append((t, values))
                if len(cols) < MIN_COLUMNS:
                    continue
                sessions[symbol] += 1
                bars = bars_by_symbol[symbol]
                close_utc = utc(d, 16, 0)
                bright_history: list[tuple[datetime, list[float]]] = []
                tops: list[float] = []
                relevant_zones: list = []
                recent: list[tuple[datetime, float, float]] = []
                for t, values in cols:
                    bar = bars.get(t) or bars.get(t - timedelta(minutes=1))
                    scale = p98(values)
                    if bar is None or scale <= 0:
                        continue
                    price = bar[2]
                    tol = TOL * price
                    zones, bright_strikes = zones_of(strikes, values, scale, tol)
                    window = [b for (bt, b) in bright_history if t - timedelta(minutes=LOOKBACK_MIN) <= bt < t]
                    bright_history.append((t, bright_strikes))
                    local = t.astimezone(ET).time()
                    if not (time(9, 45) <= local <= time(15, 30)) or len(window) < 5:
                        continue
                    candidates = zones
                    if MODE == "dominant":
                        # EXPLORATORY (after the pre-registered result): the zone of the max-|gamma| strike, new = the max sat outside it 3 columns ago and
                        # has been inside it for the last 3 columns (no look-ahead; flip-flops filtered).
                        tops.append(max(((strikes[i], abs(v)) for i, v in enumerate(values) if v is not None), key=lambda p: p[1])[0])
                        candidates = []
                        if len(tops) >= 4:
                            top = tops[-1]
                            zone = next(((zlo, zhi) for zlo, zhi in zones if zlo <= top <= zhi), (top - tol, top + tol))
                            if all(zone[0] <= k <= zone[1] for k in tops[-3:]) and not (zone[0] <= tops[-4] <= zone[1]):
                                candidates = [zone]
                    if MODE == "nearwhite":
                        # THIRD definition (addendum 11:58, written before running): the zone of the whitest strike within 0.60 % of the price, only if
                        # that strike is >= 0.85 of the column scale; new = not overlapping the relevant zone of 3 columns ago, held the last 3 columns.
                        near = [(strikes[i], abs(v) / scale) for i, v in enumerate(values) if v is not None and abs(strikes[i] - price) <= MAX_DIST * price]
                        best = max(near, key=lambda p: p[1]) if near else None
                        relevant = None
                        if best and best[1] >= 0.85:
                            relevant = next(((zlo, zhi) for zlo, zhi in zones if zlo <= best[0] <= zhi), (best[0] - tol, best[0] + tol))
                        relevant_zones.append(relevant)
                        candidates = []
                        if len(relevant_zones) >= 4 and relevant is not None:
                            held = all(z is not None and not (z[1] < relevant[0] or z[0] > relevant[1]) for z in relevant_zones[-3:])
                            before = relevant_zones[-4]
                            if held and (before is None or before[1] < relevant[0] or before[0] > relevant[1]):
                                candidates = [relevant]
                    for lo, hi in candidates:
                        if MODE == "lit" and any(lo <= k <= hi for strikes_then in window for k in strikes_then):
                            continue  # not new
                        if lo <= price <= hi:
                            continue
                        above = lo > price
                        dist = lo - price if above else price - hi
                        if not (tol <= dist <= MAX_DIST * price):
                            continue
                        if any(et >= t - timedelta(minutes=DEDUPE_MIN) and not (hi < elo or lo > ehi) for et, elo, ehi in recent):
                            continue
                        recent.append((t, lo, hi))
                        width = hi - lo
                        mirror = (price - dist - width, price - dist) if above else (price + dist, price + dist + width)
                        minutes = [t + timedelta(minutes=k) for k in range(1, max(HORIZONS) + 1) if t + timedelta(minutes=k) < close_utc]
                        ev = {"symbol": symbol, "day": d, "t": t, "above": above, "dist_pct": 100 * dist / price, "lo": lo, "hi": hi}
                        for name, (zlo, zhi), from_below in (("zone", (lo, hi), above), ("mirror", mirror, not above)):
                            k, m = first_touch(bars, minutes, zlo, zhi)
                            ev[name + "_minutes"] = k
                            ev[name + "_reaction"] = reaction(bars, m, zlo, zhi, from_below, tol, close_utc) if m else None
                        events.append(ev)
            d += timedelta(days=1)

    print("sessions per symbol:", sessions)
    print("events per symbol:", {s: sum(1 for e in events if e["symbol"] == s) for s in SYMBOLS})

    def rate(group, name, horizon):
        hits = [1 if e[name + "_minutes"] is not None and e[name + "_minutes"] <= horizon else 0 for e in group]
        return (sum(hits) / len(hits)) if hits else float("nan"), len(hits)

    def boot(group, horizon, reps=5000):
        by_day = {}
        for e in group:
            by_day.setdefault((e["symbol"], e["day"]), []).append(e)
        keys = list(by_day)
        rng = random.Random(7)
        diffs = []
        for _ in range(reps):
            sample = [e for k in (rng.choice(keys) for _ in keys) for e in by_day[k]]
            diffs.append(rate(sample, "zone", horizon)[0] - rate(sample, "mirror", horizon)[0])
        diffs.sort()
        return diffs[int(0.025 * reps)], diffs[int(0.975 * reps)]

    primary = [e for e in events if e["symbol"] in PRIMARY]
    z, n = rate(primary, "zone", 30)
    m, _ = rate(primary, "mirror", 30)
    lo, hi = boot(primary, 30)
    print(f"\nH1 (SPX+NDX, 30 min): zone {100 * z:.1f}% vs mirror {100 * m:.1f}% (n={n}) diff {100 * (z - m):+.1f} pts, 95% CI [{100 * lo:+.1f} ; {100 * hi:+.1f}]")
    print("\nSecondary:")
    for label, group in [("SPX+NDX", primary)] + [(s, [e for e in events if e["symbol"] == s]) for s in SYMBOLS]:
        if not group:
            continue
        parts = []
        for h in HORIZONS:
            zr, nn = rate(group, "zone", h)
            mr, _ = rate(group, "mirror", h)
            parts.append(f"{h}m {100 * zr:.0f}% vs {100 * mr:.0f}%")
        times = [e["zone_minutes"] for e in group if e["zone_minutes"] is not None and e["zone_minutes"] <= 30]
        print(f"  {label}: n={len(group)} | " + " | ".join(parts) + f" | median minutes to arrival (<=30) {statistics.median(times) if times else float('nan'):.0f}")
    for label, group in (("above", [e for e in primary if e["above"]]), ("below", [e for e in primary if not e["above"]])):
        zr, nn = rate(group, "zone", 30)
        mr, _ = rate(group, "mirror", 30)
        print(f"  SPX+NDX zones {label} the price: n={nn} | 30m {100 * zr:.0f}% vs mirror {100 * mr:.0f}%")
    for name in ("zone", "mirror"):
        reached = [e[name + "_reaction"] for e in primary if e[name + "_reaction"]]
        counts = {k: reached.count(k) for k in ("giro", "ruptura", "nada", "ambos")}
        total = max(1, len(reached))
        print(f"  on arrival ({name}, SPX+NDX, within 15 min): " + ", ".join(f"{k} {v} ({100 * v / total:.0f}%)" for k, v in counts.items()))
    print("\nSanity check (Lester's examples): SPX events on 11-02 and 11-06")
    for e in events:
        if e["symbol"] == "SPX" and e["day"] in (date(2026, 11, 2), date(2026, 11, 6)):
            print(f"  {e['day']} {e['t'].astimezone(ET):%H:%M} {'above' if e['above'] else 'below'} dist {e['dist_pct']:.2f}% zone_lo_hi {e.get('lo'):.1f}-{e.get('hi'):.1f} arrival {e['zone_minutes']} min, {e['zone_reaction']}")
    dists = [e["dist_pct"] for e in primary]
    print(f"  distance at ignition SPX+NDX: median {statistics.median(dists):.2f}% of price")


if __name__ == "__main__":
    main()
