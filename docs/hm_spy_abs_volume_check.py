"""READ-ONLY. SPY 0DTE by volume: the ABS strike per cycle since 09:30 vs the SPY price at that cycle (nearest strike), and the latest cycle's top 5 strikes by
|call|+|put| exposure with their traded volume. Small queries."""
import sys

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text

from backend.core.container import build_container

c = build_container()
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    s.execute(text("SET LOCAL statement_timeout='20s'"))
    rows = s.execute(text("""
        select g.time, g.absolute_gamma_strike,
               (select p.price from market_snapshots p where p.underlying_id = g.underlying_id and p.time <= g.time order by p.time desc limit 1) as px
        from gamma_aggregates g join underlyings u on u.id = g.underlying_id
        where u.symbol = 'SPY' and g.view = '0dte_volume'
          and g.time >= (date_trunc('day', now() at time zone 'America/New_York') + interval '9 hours 30 minutes') at time zone 'America/New_York'
        order by g.time""")).all()
    prices = [float(r[2]) for r in rows if r[2] is not None]
    print(f"cycles {len(rows)} | SPY price range since 09:30: {min(prices):.2f} - {max(prices):.2f}")
    for t, a, px in rows[:: max(1, len(rows) // 12)]:
        print(f"  {t.astimezone().strftime('%H:%M')} price {float(px):.2f} nearest strike {round(float(px))} abs {float(a):g}")
    s.rollback()
g = c.storage.get_latest_gamma_aggregate("SPY", view="0dte_volume")
top = sorted(g.items, key=lambda i: abs(float(i.call_gamma_exposure)) + abs(float(i.put_gamma_exposure)), reverse=True)[:5]
print("latest top 5 by |call|+|put| (volume weight; 'oi' column = traded volume):")
for i in top:
    print(f"  {float(i.strike):g}: |call|+|put| {abs(float(i.call_gamma_exposure)) + abs(float(i.put_gamma_exposure)):.3e} volume {i.open_interest}")
