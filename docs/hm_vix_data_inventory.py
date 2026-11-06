"""READ-ONLY. What VIX / SPX / NDX data is stored for the VIX -> SPX study (2026-11-05): sessions with 1-minute coverage, VIX gamma views per day,
gamma_aggregate_items columns."""
import sys

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text

from backend.core.container import build_container

c = build_container()
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    for sym in ("VIX", "SPX", "NDX"):
        r = s.execute(text(
            "select count(distinct (m.time at time zone 'America/New_York')::date), min(m.time), max(m.time), count(*) "
            "from market_snapshots m join underlyings u on u.id = m.underlying_id where u.symbol = :s"), {"s": sym}).one()
        print(sym, "days", r[0], "from", r[1], "to", r[2], "rows", r[3])
    rows = s.execute(text(
        "select (m.time at time zone 'America/New_York')::date d, count(*), "
        "count(distinct date_trunc('minute', m.time)) filter (where (m.time at time zone 'America/New_York')::time between '09:30' and '16:00') "
        "from market_snapshots m join underlyings u on u.id = m.underlying_id where u.symbol = 'VIX' group by 1 order by 1")).all()
    print("VIX per day (rows, session minutes):", " ".join(f"{d:%m-%d}:{n}/{mins}" for d, n, mins in rows))
    print("gamma_aggregate_items columns:", [r[0] for r in s.execute(text(
        "select column_name from information_schema.columns where table_name = 'gamma_aggregate_items' order by ordinal_position")).all()])
    rows = s.execute(text(
        "select g.view, count(distinct (g.time at time zone 'America/New_York')::date), min(g.time) from gamma_aggregates g join underlyings u on u.id = g.underlying_id "
        "where u.symbol = 'VIX' group by 1 order by 1")).all()
    print("VIX gamma views:", rows)
    rows = s.execute(text(
        "select g.view, count(distinct (i.time at time zone 'America/New_York')::date) from gamma_aggregate_items i join gamma_aggregates g "
        "on g.underlying_id = i.underlying_id and g.time = i.time and g.view = i.view join underlyings u on u.id = g.underlying_id where u.symbol = 'VIX' group by 1"
    )).all() if "view" in [r[0] for r in s.execute(text("select column_name from information_schema.columns where table_name = 'gamma_aggregate_items'")).all()] else "no view column"
    print("VIX items days per view:", rows)
