"""READ-ONLY. Does the ABS strike "lead" the price today? For SPX and SPY and the views 0dte, 0dte_volume, tactical: every change of the stored ABS to a new
strike K at time t (price P at t), whether the price touched K within the next 15 and 30 minutes, and how far it was at t. Descriptive, one session."""
import sys
from datetime import timedelta

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text

from backend.core.container import build_container
from backend.domain.use_cases.market_hours import EASTERN_TIME

c = build_container()
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    s.execute(text("SET LOCAL statement_timeout='30s'"))
    start = "(date_trunc('day', now() at time zone 'America/New_York') + interval '9 hours 30 minutes') at time zone 'America/New_York'"
    for symbol in ("SPX", "SPY"):
        prices = s.execute(text(f"""select p.time, p.price from market_snapshots p join underlyings u on u.id = p.underlying_id
            where u.symbol = :s and p.time >= {start} order by p.time"""), {"s": symbol}).all()
        prices = [(t, float(p)) for t, p in prices]
        last_t = prices[-1][0]
        for view in ("0dte", "0dte_volume", "tactical"):
            rows = s.execute(text(f"""select g.time, g.absolute_gamma_strike from gamma_aggregates g join underlyings u on u.id = g.underlying_id
                where u.symbol = :s and g.view = :v and g.time >= {start} order by g.time"""), {"s": symbol, "v": view}).all()
            changes, prev = [], None
            for t, a in rows:
                if prev is not None and a != prev:
                    changes.append((t, float(a)))
                prev = a
            out = []
            for t, k in changes:
                p0 = next((p for pt, p in reversed(prices) if pt <= t), None)
                if p0 is None:
                    continue
                def touched(minutes):
                    if t + timedelta(minutes=minutes) > last_t:
                        return "n/a"
                    window = [p for pt, p in prices if t < pt <= t + timedelta(minutes=minutes)]
                    return "yes" if window and min(window) <= k <= max(window) or (window and ((p0 <= k and max(window) >= k) or (p0 >= k and min(window) <= k))) else "no"
                out.append(f"{t.astimezone(EASTERN_TIME):%H:%M} -> {k:g} (price {p0:.2f}, {k - p0:+.2f}) 15m {touched(15)} 30m {touched(30)}")
            print(f"\n{symbol} {view}: {len(changes)} changes")
            for line in out[-14:]:
                print("  " + line)
    s.rollback()
