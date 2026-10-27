"""READ-ONLY. SPX 2026-10-27: Convexa's stored levels (0dte by OI, 0dte_volume, tactical) at the times of Lester's GEXBot screenshots, to compare with GEXBot's
Zero Gamma / Major Pos / Major Neg. Prints the cycle at or just before each time."""
import sys

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text

from backend.core.container import build_container

TIMES = ["09:34", "09:36", "09:40", "09:42", "09:46", "10:00", "10:10", "10:15", "10:20"]
c = build_container()
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    s.execute(text("SET LOCAL statement_timeout='20s'"))
    for hhmm in TIMES:
        parts = []
        for view in ("0dte_volume", "0dte", "tactical"):
            row = s.execute(text("""
                select g.time, g.gamma_flip, g.call_wall, g.put_wall, g.absolute_gamma_strike
                from gamma_aggregates g join underlyings u on u.id = g.underlying_id
                where u.symbol = 'SPX' and g.view = :v
                  and g.time <= (date '2026-10-27' + cast(:t as time)) at time zone 'America/New_York'
                  and g.time >= (date '2026-10-27' + time '09:30') at time zone 'America/New_York'
                order by g.time desc limit 1"""), {"v": view, "t": hhmm}).first()
            if row:
                t, flip, cw, pw, ab = row
                parts.append(f"{view} [{t.astimezone().strftime('%H:%M')}] flip {float(flip or 0):.1f} cw {float(cw or 0):g} pw {float(pw or 0):g} abs {float(ab):g}")
        print(f"{hhmm}: " + " | ".join(parts))
    s.rollback()
