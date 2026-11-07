"""READ-ONLY, EXPLORATORY (Lester 2026-11-07: "por qué el precio giró allí y no continuó?"). Finds which NinjaTrader order-flow measures separate a TURN
from a CONTINUATION when the price reaches the NEAREST white gamma zone (trading scales by distance: the nearest zone first).

- Zones (0DTE Volumen, cycle columns, what was visible that minute): strikes with brightness >= 0.85 of the column p98, contiguous strikes grouped,
  +- T (0.10 % of price). Nearest zone above and nearest below the price, within 0.60 %.
- Arrival: the index minute high/low enters one of those two zones, not touched in the previous 15 minutes (09:45-15:30).
- Outcome, from the end of the minute after the touch, 15 minutes: TURN = back out the side it came from by 2T; BREAK = out the other side by 2T;
  NOTHING otherwise (first one wins).
- Order flow (ES for SPX, NQ for NDX; NinjaTrader 1-minute footprints in V:/Convexa/hist/nt_export, same UTC minute), signed in the direction of
  travel (+ = pushing into the zone):
    approach_delta  delta / volume of the 3 minutes before the touch
    touch_delta     delta / volume of the touch minute and the next
    volume_ratio    volume of the touch minute and the next / 2 x mean volume of the 30 minutes before
    extreme_share   share of the touch minute's volume traded at the 2 ticks of its extreme in the direction of travel (absorption at the edge)
    divergence      1 when the touch minute makes a new extreme in the direction of travel but its delta is against it
    progress        how far past the zone's near edge the touch minute went, in T
Prints per-outcome medians and, per measure, the AUC of TURN vs not-TURN (0.5 = no separation). Exploratory: anything found is a hypothesis to
pre-register and confirm on sessions after 2026-11-07."""
import csv
import statistics
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
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
PAIRS = {"SPX": "ES", "NDX": "NQ"}
TICK = {"ES": 0.25, "NQ": 0.25}
FIRST, LAST = date(2026, 10, 19), date(2026, 11, 6)
WHITE, TOL, MAX_DIST = 0.85, 0.0010, 0.0060
EXPORT = Path("V:/Convexa/hist/nt_export")
MEASURES = ("approach_delta", "touch_delta", "volume_ratio", "extreme_share", "divergence", "progress")


def utc(d, h, m):
    return datetime.combine(d, time(h, m), ET).astimezone(timezone.utc)


def p98(values):
    mags = sorted(abs(v) for v in values if v is not None and v != 0)
    return mags[min(len(mags) - 1, int(0.98 * len(mags)))] if mags else 0.0


def load_footprints(future, d):
    path = EXPORT / f"{future}_{d:%Y%m%d}.csv"
    out = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8-sig") as handle:  # NinjaTrader writes a BOM
        for row in csv.DictReader(handle):
            minute = datetime.fromisoformat(row["minute_utc"].replace("Z", "+00:00"))
            levels = []
            for cell in (row.get("footprint") or "").split("|"):
                parts = cell.split(":")
                if len(parts) == 3:
                    levels.append((float(parts[0]), float(parts[1]), float(parts[2])))
            out[minute] = {"high": float(row["high"]), "low": float(row["low"]), "volume": float(row["volume"]), "delta": float(row["delta"]), "levels": levels}
    return out


def auc(pos, neg):
    if not pos or not neg:
        return float("nan")
    wins = 0.0
    for a in pos:
        for b in neg:
            wins += 1 if a > b else 0.5 if a == b else 0
    return wins / (len(pos) * len(neg))


def main():
    app = create_app()
    fake = SessionPayload(user_id=0, username="measure", is_admin=False)
    for dep in (require_session, require_session_ws, require_admin):
        app.dependency_overrides[dep] = lambda: fake
    container = build_container()
    bars = {}
    with container.storage.session_factory() as session:
        for symbol in PAIRS:
            rows = session.execute(text(
                "SELECT date_trunc('minute', m.time) AS minute, max(m.price) AS high, min(m.price) AS low, "
                "(array_agg(m.price ORDER BY m.time DESC))[1] AS close FROM market_snapshots m JOIN underlyings u ON u.id = m.underlying_id "
                "WHERE u.symbol = :symbol AND m.time >= :since GROUP BY 1"), {"symbol": symbol, "since": utc(FIRST, 0, 0)}).all()
            bars[symbol] = {r.minute.astimezone(timezone.utc): (float(r.high), float(r.low), float(r.close)) for r in rows}

    events = []
    with TestClient(app) as client:
        d = FIRST
        while d <= LAST:
            if d.weekday() >= 5:
                d += timedelta(days=1)
                continue
            for symbol, future in PAIRS.items():
                fp = load_footprints(future, d)
                if not fp:
                    continue
                r = client.get(f"/api/v1/gamma/{symbol}/heatmap", params={
                    "view": "0dte_volume", "metric": "gamma", "start": utc(d, 9, 30).isoformat().replace("+00:00", "Z"),
                    "end": utc(d, 16, 0).isoformat().replace("+00:00", "Z"), "bucket_seconds": 60, "source": "cycle"})
                if r.status_code != 200:
                    continue
                body = r.json()
                strikes = [float(s) for s in body.get("strikes") or []]
                cols = {}
                for col in body.get("columns") or []:
                    values = col.get("values") or []
                    if any(v not in (None, 0) for v in values):
                        cols[datetime.fromisoformat(str(col["as_of"]).replace("Z", "+00:00")).replace(second=0, microsecond=0)] = values
                if len(cols) < 100:
                    continue
                b = bars[symbol]
                close_utc = utc(d, 16, 0)
                last_touch = {}
                times = sorted(cols)
                for i, t in enumerate(times):
                    if not (time(9, 45) <= t.astimezone(ET).time() <= time(15, 30)):
                        continue
                    prev = b.get(t - timedelta(minutes=1))
                    if prev is None:
                        continue
                    price = prev[2]  # zones as visible at t, price at the end of the previous minute
                    tol = TOL * price
                    values = cols[t]
                    scale = p98(values)
                    white = [scale > 0 and v is not None and abs(v) / scale >= WHITE for v in values]
                    zones, k = [], 0
                    while k < len(strikes):
                        if white[k]:
                            j = k
                            while j + 1 < len(strikes) and white[j + 1]:
                                j += 1
                            zones.append((strikes[k] - tol, strikes[j] + tol))
                            k = j + 1
                        else:
                            k += 1
                    above = [z for z in zones if z[0] > price and z[0] - price <= MAX_DIST * price]
                    below = [z for z in zones if z[1] < price and price - z[1] <= MAX_DIST * price]
                    nearest = ([(min(above, key=lambda z: z[0]), 1)] if above else []) + ([(max(below, key=lambda z: z[1]), -1)] if below else [])
                    bar = b.get(t)
                    if bar is None:
                        continue
                    for (lo, hi), direction in nearest:
                        if not (bar[0] >= lo and bar[1] <= hi):
                            continue
                        key = round((lo + hi) / 2 / tol)
                        if key in last_touch and t - last_touch[key] < timedelta(minutes=15):
                            last_touch[key] = t
                            continue
                        last_touch[key] = t
                        # outcome from the end of the minute after the touch
                        outcome, m = "nothing", t + timedelta(minutes=1)
                        for _ in range(15):
                            m += timedelta(minutes=1)
                            if m >= close_utc:
                                break
                            x = b.get(m)
                            if not x:
                                continue
                            back = x[1] <= lo - 2 * tol if direction == 1 else x[0] >= hi + 2 * tol
                            through = x[0] >= hi + 2 * tol if direction == 1 else x[1] <= lo - 2 * tol
                            if back or through:
                                outcome = "turn" if back and not through else "break" if through and not back else "both"
                                break
                        # order flow of the future, same UTC minutes
                        f_touch = [fp.get(t + timedelta(minutes=k)) for k in (0, 1)]
                        f_before = [fp.get(t - timedelta(minutes=k)) for k in (1, 2, 3)]
                        f_base = [fp.get(t - timedelta(minutes=k)) for k in range(1, 31)]
                        if any(x is None for x in f_touch + f_before) or sum(1 for x in f_base if x) < 20:
                            continue
                        vol_touch = sum(x["volume"] for x in f_touch)
                        vol_before = sum(x["volume"] for x in f_before)
                        base = statistics.mean(x["volume"] for x in f_base if x)
                        touch0 = f_touch[0]
                        tick = TICK[future]
                        if direction == 1:
                            edge = [v for v in touch0["levels"] if v[0] >= touch0["high"] - tick * 1.01]
                            new_extreme = touch0["high"] > max(x["high"] for x in f_before)
                        else:
                            edge = [v for v in touch0["levels"] if v[0] <= touch0["low"] + tick * 1.01]
                            new_extreme = touch0["low"] < min(x["low"] for x in f_before)
                        edge_volume = sum(v[1] + v[2] for v in edge)
                        reach = (bar[0] - lo) if direction == 1 else (hi - bar[1])
                        events.append({
                            "symbol": symbol, "day": d, "t": t, "outcome": outcome, "direction": direction,
                            "approach_delta": direction * sum(x["delta"] for x in f_before) / max(1, vol_before),
                            "touch_delta": direction * sum(x["delta"] for x in f_touch) / max(1, vol_touch),
                            "volume_ratio": vol_touch / max(1e-9, 2 * base),
                            "extreme_share": edge_volume / max(1, touch0["volume"]),
                            "divergence": 1.0 if new_extreme and direction * touch0["delta"] < 0 else 0.0,
                            "progress": reach / tol,
                        })
            d += timedelta(days=1)

    print("arrivals:", len(events), {s: sum(1 for e in events if e["symbol"] == s) for s in PAIRS})
    for name in ("turn", "break", "nothing", "both"):
        print(f"  {name}: {sum(1 for e in events if e['outcome'] == name)}")
    print("\nmedians by outcome (signed in the direction of travel):")
    for measure in MEASURES:
        line = [f"{measure:15s}"]
        for name in ("turn", "break", "nothing"):
            vals = [e[measure] for e in events if e["outcome"] == name]
            line.append(f"{name} {statistics.median(vals):+.3f}" if vals else f"{name} -")
        turn = [e[measure] for e in events if e["outcome"] == "turn"]
        rest = [e[measure] for e in events if e["outcome"] in ("break", "nothing")]
        brk = [e[measure] for e in events if e["outcome"] == "break"]
        line.append(f"AUC turn vs rest {auc(turn, rest):.2f}")
        line.append(f"AUC turn vs break {auc(turn, brk):.2f}")
        print("  " + " | ".join(line))
    print("\nexamples (SPX 11-06):")
    for e in events:
        if e["symbol"] == "SPX" and e["day"] == date(2026, 11, 6):
            print(f"  {e['t'].astimezone(ET):%H:%M} dir {e['direction']:+d} {e['outcome']:8s} approach {e['approach_delta']:+.2f} touch {e['touch_delta']:+.2f} "
                  f"vol x{e['volume_ratio']:.1f} edge {e['extreme_share']:.2f} div {int(e['divergence'])} progress {e['progress']:.1f}T")


if __name__ == "__main__":
    main()
