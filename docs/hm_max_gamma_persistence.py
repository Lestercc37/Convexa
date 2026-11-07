"""READ-ONLY study (Lester 2026-11-06 22:00, run 2026-11-07): how long does the strongest-gamma strike stay the strongest before it changes? Answers whether
the 10-60 s delay of the gamma map in NinjaTrader matters. For SPX and NDX, views 0dte_volume and 0dte, metric gamma, every stored session 09:30-16:00 ET:

1. runs of the max-|gamma| strike (consecutive columns with the same strike): median and p25 duration in minutes, changes per hour, the share of the session spent in
   runs of 10+ minutes, and "flip-flops" (the max moves away and comes back within 3 minutes);
2. the same for the set the old Lineas style showed: the 3 strongest strikes under the price and the 3 above (price = the 1-minute close at that column).

Sources: the cycle columns (60 s buckets, every session stored) and the fast layer (10 s buckets) where it has rows. In-process API reads (TestClient, session gate
overridden) and the stored 1-minute candles; nothing is written. Prints a report."""
import asyncio
import statistics
import sys
from collections import Counter
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
FIRST, LAST = date(2026, 10, 19), date(2026, 11, 6)
HOLIDAYS: set[date] = set()


def days():
    d = FIRST
    while d <= LAST:
        if d.weekday() < 5 and d not in HOLIDAYS:
            yield d
        d += timedelta(days=1)


def pct(values, q):
    values = sorted(values)
    if not values:
        return float("nan")
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def weighted_median(durations):
    """The run length a typical MINUTE of the session belongs to (each run weighted by its own length)."""
    ordered = sorted(durations)
    half, acc = sum(ordered) / 2, 0.0
    for d in ordered:
        acc += d
        if acc >= half:
            return d
    return float("nan")


def runs_of(keys, times, bucket):
    """[(key, start, end)] of consecutive equal keys; end = last time + bucket."""
    out = []
    for key, t in zip(keys, times):
        if out and out[-1][0] == key and (t - out[-1][2]).total_seconds() <= bucket * 1.5:
            out[-1] = (key, out[-1][1], t + timedelta(seconds=bucket))
        else:
            out.append((key, t, t + timedelta(seconds=bucket)))
    return out


def columns_for(client, symbol, view, source, bucket, day):
    start = datetime.combine(day, time(9, 30), ET).astimezone(timezone.utc)
    end = datetime.combine(day, time(16, 0), ET).astimezone(timezone.utc)
    r = client.get(f"/api/v1/gamma/{symbol}/heatmap", params={
        "view": view, "metric": "gamma", "start": start.isoformat().replace("+00:00", "Z"), "end": end.isoformat().replace("+00:00", "Z"),
        "bucket_seconds": bucket, "source": source})
    if r.status_code != 200:
        return [], []
    body = r.json()
    strikes = [float(s) for s in body.get("strikes") or []]
    cols = []
    for col in body.get("columns") or []:
        values = col.get("values") or []
        if not any(v not in (None, 0) for v in values):
            continue  # all-zero / empty column (0DTE expired at 16:00)
        t = datetime.fromisoformat(str(col["as_of"]).replace("Z", "+00:00"))
        cols.append((t, values))
    return strikes, cols


def main():
    app = create_app()
    fake = SessionPayload(user_id=0, username="measure", is_admin=False)
    for dep in (require_session, require_session_ws, require_admin):
        app.dependency_overrides[dep] = lambda: fake
    container = build_container()
    closes: dict[str, dict[datetime, float]] = {}
    since = datetime.combine(FIRST, time(0, 0), ET).astimezone(timezone.utc)
    with container.storage.session_factory() as session:  # sync reads: the async pool belongs to the app's own loop
        for symbol in ("SPX", "NDX"):
            rows = session.execute(text(
                "SELECT date_trunc('minute', m.time) AS minute, (array_agg(m.price ORDER BY m.time DESC))[1] AS close "
                "FROM market_snapshots m JOIN underlyings u ON u.id = m.underlying_id "
                "WHERE u.symbol = :symbol AND m.time >= :since GROUP BY 1"), {"symbol": symbol, "since": since}).all()
            closes[symbol] = {row.minute.astimezone(timezone.utc).replace(second=0, microsecond=0): float(row.close) for row in rows}

    with TestClient(app) as client:
        for symbol in ("SPX", "NDX"):
            for view in ("0dte_volume", "0dte"):
                for source, bucket in (("cycle", 60), ("fast", 10)):
                    run_minutes, changes_per_hour, long_share, flips, sessions = [], [], [], 0, 0
                    set_run_minutes, set_changes_per_hour = [], []
                    for day in days():
                        strikes, cols = columns_for(client, symbol, view, source, bucket, day)
                        if len(cols) < 30:
                            continue
                        sessions += 1
                        times = [t for t, _ in cols]
                        tops = []
                        sets = []
                        for t, values in cols:
                            pairs = [(strikes[i], abs(v)) for i, v in enumerate(values) if v is not None]
                            tops.append(max(pairs, key=lambda p: p[1])[0])
                            minute = t.replace(second=0, microsecond=0)
                            price = closes[symbol].get(minute) or closes[symbol].get(minute - timedelta(minutes=1))
                            if price is None:
                                sets.append(None)
                                continue
                            below = sorted((p for p in pairs if p[0] <= price), key=lambda p: -p[1])[:3]
                            above = sorted((p for p in pairs if p[0] > price), key=lambda p: -p[1])[:3]
                            sets.append(frozenset(p[0] for p in below + above))
                        hours = (times[-1] - times[0]).total_seconds() / 3600 or 1
                        runs = runs_of(tops, times, bucket)
                        durations = [(e - s).total_seconds() / 60 for _, s, e in runs]
                        run_minutes += durations
                        changes_per_hour.append((len(runs) - 1) / hours)
                        total = sum(durations)
                        long_share.append(sum(d for d in durations if d >= 10) / total if total else 0)
                        for a, b, c in zip(runs, runs[1:], runs[2:]):
                            if a[0] == c[0] and (b[2] - b[1]).total_seconds() <= 180:
                                flips += 1
                        # membership runs of each strike in the 3+3 set
                        member = Counter()
                        for strike in {k for s in sets if s for k in s}:
                            flags = [bool(s and strike in s) for s in sets]
                            for on, s0, e0 in runs_of(flags, times, bucket):
                                if on:
                                    set_run_minutes.append((e0 - s0).total_seconds() / 60)
                        set_changes = sum(1 for a, b in zip(sets, sets[1:]) if a and b and a != b)
                        set_changes_per_hour.append(set_changes / hours)
                    if not sessions:
                        print(f"{symbol} {view} {source}: no sessions")
                        continue
                    print(
                        f"{symbol} {view} {source} ({bucket}s) sessions={sessions} | MAX strike: runs={len(run_minutes)} "
                        f"median={statistics.median(run_minutes):.1f} min p25={pct(run_minutes, .25):.1f} p75={pct(run_minutes, .75):.1f} "
                        f"changes/hour median={statistics.median(changes_per_hour):.1f} | time in runs >=10 min={100 * statistics.median(long_share):.0f}% | time-weighted run median={weighted_median(run_minutes):.0f} min "
                        f"| flip-flops (back within 3 min)={flips} ({100 * flips / max(1, len(run_minutes)):.0f}% of runs) "
                        f"|| 3+3 SET: membership median={statistics.median(set_run_minutes):.1f} min p25={pct(set_run_minutes, .25):.1f} "
                        f"set changes/hour median={statistics.median(set_changes_per_hour):.1f}"
                    )


if __name__ == "__main__":
    main()
