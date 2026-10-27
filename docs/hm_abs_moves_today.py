"""READ-ONLY. Does the ABS Gamma strike move today? Per symbol and view: cycles stored since 09:30 ET, the latest cycle time, and the ABS / Call Wall / Put Wall
values seen today in order (changes only). Small query on gamma_aggregates."""
import sys

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text

from backend.core.container import build_container

c = build_container()
SQL = """
select g.time, g.absolute_gamma_strike, g.call_wall, g.put_wall
from gamma_aggregates g join underlyings u on u.id = g.underlying_id
where u.symbol = :s and g.view = :v and g.time >= (date_trunc('day', now() at time zone 'America/New_York') + interval '9 hours 30 minutes') at time zone 'America/New_York'
order by g.time
"""
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    s.execute(text("SET LOCAL statement_timeout='20s'"))
    for symbol in ["SPX", "SPY", "QQQ", "NDX"]:
        for view in ["0dte", "0dte_volume", "tactical"]:
            rows = s.execute(text(SQL), {"s": symbol, "v": view}).all()
            if not rows:
                print(f"{symbol} {view}: no cycles since 09:30")
                continue
            seq, last = [], None
            for t, a, cw, pw in rows:
                key = (float(a), float(cw or 0), float(pw or 0))
                if key != last:
                    seq.append(f"{t.astimezone().strftime('%H:%M')} abs {key[0]:g} cw {key[1]:g} pw {key[2]:g}")
                    last = key
            print(f"{symbol} {view}: {len(rows)} cycles, last {rows[-1][0].astimezone().strftime('%H:%M:%S')} | " + " -> ".join(seq[-6:]))
    s.rollback()
