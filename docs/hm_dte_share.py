"""READ-ONLY: in the latest stored chain snapshot per symbol, how many contracts (and expirations) fall within 30/45/90 days versus beyond (LEAPS and the rest)."""
import sys
from datetime import date
from sqlalchemy import text
sys.path.insert(0, "V:/Convexa/repo")
from backend.core.container import build_container
c = build_container()
today = date(2026, 10, 15)
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY")); s.execute(text("SET LOCAL statement_timeout='60s'"))
    for sym in ("SPX", "NDX", "SPY", "QQQ", "IWM", "NVDA", "VIX"):
        uid = s.execute(text("select id from underlyings where symbol=:s"), {"s": sym}).scalar()
        last = s.execute(text("select max(time) from option_chain_snapshots where underlying_id=:u"), {"u": uid}).scalar()
        rows = s.execute(text("select oc.expiration, count(*) from option_chain_snapshots sn join option_contracts oc on oc.id=sn.contract_id where sn.underlying_id=:u and sn.time=:t group by 1"), {"u": uid, "t": last}).all()
        tot = sum(r[1] for r in rows)
        def within(d):
            return sum(r[1] for r in rows if (r[0] - today).days <= d), sum(1 for r in rows if (r[0] - today).days <= d)
        w30, e30 = within(30); w45, e45 = within(45); w90, e90 = within(90)
        print("%-5s contratos %5d en %2d vencimientos | <=30d %5d (%2d venc, %.0f%%) | <=45d %5d (%2d, %.0f%%) | <=90d %5d (%2d, %.0f%%) | mas alla de 90d %5d (%.0f%%)" % (
            sym, tot, len(rows), w30, e30, 100.0 * w30 / tot, w45, e45, 100.0 * w45 / tot, w90, e90, 100.0 * w90 / tot, tot - w90, 100.0 * (tot - w90) / tot))
    s.rollback()
