"""READ-ONLY. How often the 0-2 DTE (tactical, OI) Call Wall and Put Wall sit together (|cw - pw| <= 2 dollars) for SPY and QQQ, per session since 2026-09-25,
and what the price did on those days: share of the session's cycles with the walls together, first time it happened, the session range (high - low), and after
the first "together" cycle, how much of the rest of the session the price stayed within 1.5 dollars of the walls' midpoint and how far it went from it."""
import sys
from collections import defaultdict

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text

from backend.core.container import build_container
from backend.domain.use_cases.market_hours import EASTERN_TIME

TOGETHER = 2.0
NEAR = 1.5
c = build_container()
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    s.execute(text("SET LOCAL statement_timeout='60s'"))
    for symbol in ("SPY", "QQQ"):
        levels = s.execute(text("""
            select g.time, g.call_wall, g.put_wall from gamma_aggregates g join underlyings u on u.id = g.underlying_id
            where u.symbol = :s and g.view = 'tactical' and g.time >= '2026-09-25'
              and (g.time at time zone 'America/New_York')::time between '09:30' and '16:00'
            order by g.time"""), {"s": symbol}).all()
        prices = s.execute(text("""
            select p.time, p.price from market_snapshots p join underlyings u on u.id = p.underlying_id
            where u.symbol = :s and p.time >= '2026-09-25' and (p.time at time zone 'America/New_York')::time between '09:30' and '16:00'
            order by p.time"""), {"s": symbol}).all()
        by_day_levels, by_day_prices = defaultdict(list), defaultdict(list)
        for t, cw, pw in levels:
            by_day_levels[t.astimezone(EASTERN_TIME).date()].append((t, cw, pw))
        for t, p in prices:
            by_day_prices[t.astimezone(EASTERN_TIME).date()].append((t, float(p)))
        print(f"\n{symbol}: day | cycles together | first together | session range | after first: share of time within {NEAR} of the midpoint, max distance")
        together_ranges, other_ranges = [], []
        for day in sorted(by_day_levels):
            rows = by_day_levels[day]
            px = by_day_prices.get(day, [])
            if not px or not rows:
                continue
            rng = max(p for _, p in px) - min(p for _, p in px)
            together = [(t, cw, pw) for t, cw, pw in rows if cw is not None and pw is not None and abs(float(cw) - float(pw)) <= TOGETHER]
            share = len(together) / len(rows)
            line = f"  {day} | {100 * share:5.1f} % of {len(rows):3d} | "
            if together:
                t0, cw0, pw0 = together[0]
                mid = (float(cw0) + float(pw0)) / 2
                after = [p for t, p in px if t >= t0]
                near = sum(1 for p in after if abs(p - mid) <= NEAR) / max(1, len(after))
                far = max(abs(p - mid) for p in after) if after else 0
                line += f"{t0.astimezone(EASTERN_TIME):%H:%M} (mid {mid:g}) | range {rng:5.2f} | within {100 * near:5.1f} %, max {far:5.2f}"
            else:
                line += f"   -   | range {rng:5.2f} |"
            (together_ranges if share >= 0.25 else other_ranges).append(rng)
            print(line)
        def avg(v):
            return sum(v) / len(v) if v else float("nan")
        print(f"  days with walls together >= 25 % of the session: {len(together_ranges)}, mean range {avg(together_ranges):.2f} | other days: {len(other_ranges)}, mean range {avg(other_ranges):.2f}")
    s.rollback()
