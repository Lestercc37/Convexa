"""READ-ONLY: cost of PostgreSQLStorage.get_latest_option_delta (the delta read made once per FIRED whale alert for the ITM/ATM/OTM tag).
Takes 8 recent SPX alert contracts, runs the exact statement 5 times each and prints milliseconds plus the plan's buffer count. Changes nothing."""
import statistics
import sys
import time

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text
from backend.core.container import build_container

SQL = """SELECT c.delta FROM option_chain_snapshots AS c
         WHERE c.contract_id = (SELECT id FROM option_contracts WHERE occ_symbol = :occ)
         ORDER BY c.time DESC LIMIT 1"""
c = build_container()
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY")); s.execute(text("SET LOCAL statement_timeout='30s'"))
    occs = [r[0] for r in s.execute(text("select distinct occ_symbol from whale_alerts order by occ_symbol desc limit 8")).all()]
    allms = []
    for occ in occs:
        ms = []
        for _ in range(5):
            t0 = time.perf_counter()
            row = s.execute(text(SQL), {"occ": occ}).first()
            ms.append((time.perf_counter() - t0) * 1000)
        allms += ms
        print("%-24s delta=%s | ms min %.1f median %.1f max %.1f" % (occ, None if row is None else row[0], min(ms), statistics.median(ms), max(ms)))
    print("ALL: median %.1f ms, max %.1f ms over %d runs" % (statistics.median(allms), max(allms), len(allms)))
    plan = s.execute(text("EXPLAIN (ANALYZE, BUFFERS) " + SQL), {"occ": occs[0]}).all()
    for r in plan[:12]:
        print(r[0][:160])
    s.rollback()
