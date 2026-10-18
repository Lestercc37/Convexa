"""READ-ONLY: NDX contracts for today's expiration inside the Smile window (+-1.5% of spot): the ones whose IV sits far from their neighbours, with bid/ask, to explain the stray red dots on the Volatility Smile."""
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, "V:/Convexa/repo")
from backend.core.container import build_container
ET = ZoneInfo("America/New_York")
c = build_container()
chain = c.storage.get_latest_chain_snapshot("NDX")
spot = float(chain.spot_price)
today = chain.as_of.astimezone(ET).date()
rows = []
for k in chain.contracts:
    if k.expiration != today:
        continue
    if abs(float(k.strike) - spot) > 0.015 * spot:
        continue
    root = re.match(r"^([A-Z]+)", k.occ_symbol).group(1)
    rows.append((float(k.strike), "C" if k.contract_type.value.lower().startswith("c") else "P", float(k.iv), float(k.bid), float(k.ask), root, k.volume))
print("NDX chain as_of", chain.as_of.astimezone(ET).strftime("%H:%M:%S"), "spot", spot, "| contracts in window:", len(rows), "| roots:", sorted({r[5] for r in rows}))
for side in ("P", "C"):
    seq = sorted((r for r in rows if r[1] == side), key=lambda r: r[0])
    print("--", side, "count", len(seq), "| IV=0:", sum(1 for r in seq if r[2] == 0))
    ivs = [r[2] for r in seq if r[2] > 0]
    for i, r in enumerate(seq):
        if r[2] <= 0:
            continue
        neigh = [x[2] for x in seq[max(0, i - 3): i + 4] if x is not r and x[2] > 0]
        if len(neigh) >= 3 and abs(r[2] - sorted(neigh)[len(neigh) // 2]) > 0.06:
            print("   outlier strike %.0f IV %.3f vs neighbours median %.3f | bid %.2f ask %.2f | volume %s" % (r[0], r[2], sorted(neigh)[len(neigh) // 2], r[3], r[4], r[6]))
