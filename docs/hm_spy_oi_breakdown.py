"""READ-ONLY. Is SPY's 0-2 DTE open interest inflated (old / expired / duplicated contracts)? From the latest stored SPY chain: expirations present, any
expiration before today, duplicated (expiration, strike, type) rows, and the call / put OI per expiration at strikes 762-772. Same for SPX at 7650-7725 for comparison."""
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "V:/Convexa/repo")

from backend.core.container import build_container
from backend.domain.entities import ContractType
from backend.domain.use_cases.market_hours import EASTERN_TIME

c = build_container()
for symbol, lo, hi in (("SPY", 762, 772), ("SPX", 7650, 7725)):
    chain = c.storage.get_latest_chain_snapshot(symbol)
    today = chain.as_of.astimezone(EASTERN_TIME).date()
    exps = sorted({k.expiration for k in chain.contracts})
    dup = Counter((k.expiration, k.strike, k.contract_type) for k in chain.contracts)
    dups = sum(1 for v in dup.values() if v > 1)
    roots = Counter(k.occ_symbol[: len(k.occ_symbol) - 15] for k in chain.contracts)
    print(f"\n{symbol} chain {chain.as_of.astimezone(EASTERN_TIME):%H:%M} today {today} | contracts {len(chain.contracts)} | expirations {len(exps)}: first {exps[:4]} | before today: {sum(1 for e in exps if e < today)} | duplicated rows: {dups} | roots {dict(roots)}")
    table = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for k in chain.contracts:
        if lo <= float(k.strike) <= hi and k.expiration <= exps[min(3, len(exps) - 1)]:
            table[float(k.strike)][k.expiration][0 if k.contract_type == ContractType.CALL else 1] += k.open_interest
    for strike in sorted(table):
        print(f"  {strike:g}: " + " | ".join(f"{e:%m-%d} C {v[0]:>6} P {v[1]:>6}" for e, v in sorted(table[strike].items())))
