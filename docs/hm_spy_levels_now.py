"""READ-ONLY. SPY now: every level of each view (flip, call wall, put wall, ABS, max pain) and the price, plus the last 10 tactical cycles' levels."""
import sys

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text

from backend.core.container import build_container
from backend.domain.use_cases.market_hours import EASTERN_TIME

c = build_container()
price = c.storage.get_latest_price("SPY")
print(f"SPY price {float(price.price):.2f} at {price.as_of.astimezone(EASTERN_TIME):%H:%M:%S}")
for view in ("tactical", "0dte", "0dte_volume", "1dte"):
    g = c.storage.get_latest_gamma_aggregate("SPY", view=view)
    print(f"  {view:12s} {g.as_of.astimezone(EASTERN_TIME):%H:%M} flip {float(g.gamma_flip or 0):.2f} cw {g.call_wall} pw {g.put_wall} abs {g.absolute_gamma_strike} maxpain {g.max_pain} width {g.near_the_money_width}")
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    rows = s.execute(text("""select g.time, g.gamma_flip, g.call_wall, g.put_wall, g.absolute_gamma_strike from gamma_aggregates g join underlyings u on u.id = g.underlying_id
        where u.symbol = 'SPY' and g.view = 'tactical' order by g.time desc limit 10""")).all()
    for t, f, cw, pw, a in rows:
        print(f"    {t.astimezone(EASTERN_TIME):%H:%M} flip {float(f or 0):.2f} cw {float(cw or 0):g} pw {float(pw or 0):g} abs {float(a):g}")
    s.rollback()
