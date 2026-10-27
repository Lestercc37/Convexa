"""READ-ONLY. Data for the desktop-app visual mockup: SPX 1-minute candles of 2026-10-27 09:30-16:00 from market_snapshots, the latest 0DTE levels and per-strike
profile near the close, and the expected move. Prints one JSON object."""
import json
import sys

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text

from backend.core.container import build_container
from backend.domain.use_cases.market_hours import EASTERN_TIME

c = build_container()
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    s.execute(text("SET LOCAL statement_timeout='60s'"))
    rows = s.execute(text("""
        select date_trunc('minute', p.time) m,
               (array_agg(p.price order by p.time))[1] o, max(p.price) h, min(p.price) l, (array_agg(p.price order by p.time desc))[1] c
        from market_snapshots p join underlyings u on u.id = p.underlying_id
        where u.symbol = 'SPX' and (p.time at time zone 'America/New_York')::date = date '2026-10-27'
          and (p.time at time zone 'America/New_York')::time between '09:30' and '15:59:59'
        group by 1 order by 1""")).all()
    s.rollback()
candles = [{"t": int(m.timestamp()), "o": float(o), "h": float(h), "l": float(l), "c": float(cl)} for m, o, h, l, cl in rows]
g = c.storage.get_latest_gamma_aggregate("SPX", view="0dte")
items = sorted(g.items, key=lambda i: i.strike)
spot = candles[-1]["c"] if candles else None
profile = [
    {"k": float(i.strike), "call": float(i.call_gamma_exposure), "put": float(i.put_gamma_exposure)}
    for i in items if spot is not None and abs(float(i.strike) - spot) <= 80
]
out = {
    "symbol": "SPX",
    "as_of": g.as_of.astimezone(EASTERN_TIME).strftime("%Y-%m-%d %H:%M"),
    "candles": candles,
    "levels": {"call_wall": float(g.call_wall or 0), "put_wall": float(g.put_wall or 0), "flip": float(g.gamma_flip or 0), "abs": float(g.absolute_gamma_strike)},
    "net_gamma": float(g.net_gamma),
    "profile": profile,
}
print(json.dumps(out))
