"""READ-ONLY: real resolution of the stored heatmap data (what GET /gamma/{symbol}/heatmap would return) for the last sessions: snapshots per day, strikes per snapshot, strike step,
time gap between snapshots, and the candle price range of the day, to estimate the on-screen size of one cell. Counts only."""
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text
from backend.core.container import build_container

ET = ZoneInfo("America/New_York")
c = build_container()
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY")); s.execute(text("SET LOCAL statement_timeout='60s'"))
    cols = [r[0] for r in s.execute(text("select column_name from information_schema.columns where table_name='gamma_aggregate_items' order by ordinal_position"))]
    print("gamma_aggregate_items columns:", cols)
    uid = s.execute(text("select id from underlyings where symbol='SPX'")).scalar()
    tcol = "time" if "time" in cols else cols[0]
    for day in ("2026-10-16", "2026-10-15", "2026-10-14"):
        a = datetime.fromisoformat(day + "T09:30:00").replace(tzinfo=ET)
        b = datetime.fromisoformat(day + "T16:00:00").replace(tzinfo=ET)
        rows = s.execute(text(f"select {tcol}, count(*), min(strike), max(strike) from gamma_aggregate_items where underlying_id=:u and view='tactical' and {tcol}>=:a and {tcol}<=:b group by {tcol} order by {tcol}"),
                         {"u": uid, "a": a, "b": b}).fetchall()
        if not rows:
            print(day, "no rows"); continue
        gaps = sorted((rows[i + 1][0] - rows[i][0]).total_seconds() for i in range(len(rows) - 1))
        n = [r[1] for r in rows]
        steps = s.execute(text(f"select strike from gamma_aggregate_items where underlying_id=:u and view='tactical' and {tcol}=:t order by strike"), {"u": uid, "t": rows[len(rows) // 2][0]}).fetchall()
        st = sorted({float(steps[i + 1][0]) - float(steps[i][0]) for i in range(len(steps) - 1)})
        px = s.execute(text("select min(price), max(price) from market_snapshots where underlying_id=:u and time>=:a and time<=:b"), {"u": uid, "a": a, "b": b}).first()
        print("%s | snapshots %d | strikes per snapshot min %d median %d max %d | strike range of the middle snapshot %s-%s | strike steps seen %s | gap between snapshots s: median %.0f p90 %.0f max %.0f | SPX price range %.1f-%.1f (%.1f pts)" % (
            day, len(rows), min(n), sorted(n)[len(n) // 2], max(n), steps[0][0], steps[-1][0], st[:4], gaps[len(gaps) // 2], gaps[int(0.9 * (len(gaps) - 1))], gaps[-1], float(px[0]), float(px[1]), float(px[1]) - float(px[0])))
    s.rollback()
