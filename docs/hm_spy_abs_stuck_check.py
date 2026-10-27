"""READ-ONLY. Why SPY's ABS (0-2 DTE, OI) does not move today: every ~15 min, the price, the top 3 strikes by |call|+|put| gamma exposure and their ratio to the
first; plus whether SPY's stored chain snapshot is fresh (as_of, spot) and whether its 0-2 DTE open interest changed during the day."""
import sys

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text

from backend.core.container import build_container
from backend.domain.use_cases.market_hours import EASTERN_TIME

c = build_container()
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    s.execute(text("SET LOCAL statement_timeout='30s'"))
    times = s.execute(text("""
        select g.time from gamma_aggregates g join underlyings u on u.id = g.underlying_id
        where u.symbol = 'SPY' and g.view = 'tactical'
          and g.time >= (date_trunc('day', now() at time zone 'America/New_York') + interval '9 hours 30 minutes') at time zone 'America/New_York'
        order by g.time""")).scalars().all()
    for t in times[:: max(1, len(times) // 10)] + [times[-1]]:
        items = s.execute(text("""
            select i.strike, abs(i.call_gamma_exposure) + abs(i.put_gamma_exposure) as tot, i.open_interest
            from gamma_aggregate_items i join underlyings u on u.id = i.underlying_id
            where u.symbol = 'SPY' and i.view = 'tactical' and i.time = :t order by tot desc limit 3"""), {"t": t}).all()
        px = s.execute(text("""select p.price from market_snapshots p join underlyings u on u.id = p.underlying_id
            where u.symbol = 'SPY' and p.time <= :t order by p.time desc limit 1"""), {"t": t}).scalar()
        first = float(items[0][1])
        print(f"{t.astimezone(EASTERN_TIME):%H:%M} price {float(px):.2f} | " + " ".join(f"{float(k):g}={100 * float(v) / first:.0f}% (OI {oi})" for k, v, oi in items))
    s.rollback()
chain = c.storage.get_latest_chain_snapshot("SPY")
print(f"latest SPY chain as_of {chain.as_of.astimezone(EASTERN_TIME):%H:%M:%S} spot {float(chain.spot_price):.2f} contracts {len(chain.contracts)}")
