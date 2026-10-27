"""READ-ONLY. How often the stored ABS Gamma strike changes per hour (view tactical) on 2026-10-26 and 2026-10-27, SPY and SPX: changes between consecutive cycles."""
import sys

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text

from backend.core.container import build_container

c = build_container()
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    s.execute(text("SET LOCAL statement_timeout='20s'"))
    for symbol in ("SPY", "SPX"):
        for day in ("2026-10-26", "2026-10-27"):
            rows = s.execute(text("""
                select extract(hour from g.time at time zone 'America/New_York')::int h, g.absolute_gamma_strike
                from gamma_aggregates g join underlyings u on u.id = g.underlying_id
                where u.symbol = :s and g.view = 'tactical' and (g.time at time zone 'America/New_York')::date = cast(:d as date)
                  and (g.time at time zone 'America/New_York')::time between '09:30' and '16:00'
                order by g.time"""), {"s": symbol, "d": day}).all()
            per_hour, last = {}, None
            for h, a in rows:
                per_hour.setdefault(h, [0, set()])
                if last is not None and a != last:
                    per_hour[h][0] += 1
                per_hour[h][1].add(float(a))
                last = a
            print(f"{symbol} {day}: " + " | ".join(f"{h}h {v[0]} changes {sorted(v[1])}" for h, v in sorted(per_hour.items())))
    s.rollback()
