"""READ-ONLY, light: how far out the stored chain snapshots reach (latest snapshot per symbol: expirations, max DTE, contracts), to see whether a 90-day aggregate view
can be built from what the scheduler already fetches."""
import sys

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text

from backend.core.container import build_container

c = build_container()
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    s.execute(text("SET LOCAL statement_timeout = '60s'"))
    for sym in ("SPX", "NDX", "SPY", "QQQ"):
        row = s.execute(text("""
            with snap as (select max(s.time) t from option_chain_snapshots s join underlyings u on u.id = s.underlying_id
                          where u.symbol = :s and s.time > now() - interval '3 days')
            select count(*), count(distinct oc.expiration), min(oc.expiration), max(oc.expiration), max(snap.t)
            from option_chain_snapshots s join option_contracts oc on oc.id = s.contract_id join underlyings u on u.id = s.underlying_id, snap
            where u.symbol = :s and s.time = snap.t"""), {"s": sym}).one()
        print(f"{sym}: latest snapshot {row[4]} | contracts {row[0]} | expirations {row[1]} from {row[2]} to {row[3]}")
    s.rollback()
