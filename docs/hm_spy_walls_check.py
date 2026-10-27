"""READ-ONLY. SPY 0-2 DTE (tactical) walls one dollar apart: per strike near the price, the call and put gamma exposure, the net, and the call / put open
interest summed over the 0-2 DTE expirations of the latest chain. Small reads."""
import sys
from collections import defaultdict
from datetime import timedelta

sys.path.insert(0, "V:/Convexa/repo")

from backend.core.container import build_container
from backend.domain.entities import ContractType
from backend.domain.use_cases.market_hours import EASTERN_TIME

c = build_container()
g = c.storage.get_latest_gamma_aggregate("SPY", view="tactical")
chain = c.storage.get_latest_chain_snapshot("SPY")
today = chain.as_of.astimezone(EASTERN_TIME).date()
oi = defaultdict(lambda: [0, 0])
for k in chain.contracts:
    if today <= k.expiration <= today + timedelta(days=2):
        oi[float(k.strike)][0 if k.contract_type == ContractType.CALL else 1] += k.open_interest
spot = float(chain.spot_price)
print(f"SPY tactical {g.as_of.astimezone(EASTERN_TIME):%H:%M} spot {spot:.2f} call wall {g.call_wall} put wall {g.put_wall} abs {g.absolute_gamma_strike}")
for i in sorted(g.items, key=lambda i: i.strike):
    if abs(float(i.strike) - spot) <= 6:
        net = float(i.call_gamma_exposure) + float(i.put_gamma_exposure)
        co, po = oi[float(i.strike)]
        print(f"  {float(i.strike):g}: call {float(i.call_gamma_exposure):+.2e} put {float(i.put_gamma_exposure):+.2e} net {net:+.2e} | call OI {co} put OI {po}")
