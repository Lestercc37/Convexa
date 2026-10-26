"""READ-ONLY. Why the GEX profile alternates green/red strike by strike near spot: per strike of the latest stored aggregate (0dte and tactical), the call and put gamma
exposure, the net, and the open interest behind each side (from the latest chain snapshot, today's expiration). Counts sign changes of the net between neighbouring strikes."""
import sys
from collections import defaultdict

sys.path.insert(0, "V:/Convexa/repo")

from backend.core.container import build_container
from backend.domain.entities import ContractType
from backend.domain.use_cases.gamma import listed_expiration_for_view

c = build_container()
for symbol, half in (("SPX", 40), ("QQQ", 8), ("SPY", 6)):
    chain = c.storage.get_latest_chain_snapshot(symbol)
    spot = float(chain.spot_price)
    exp0 = listed_expiration_for_view(chain, "0dte")
    oi = defaultdict(lambda: [0, 0])
    for k in chain.contracts:
        if k.expiration == exp0:
            oi[float(k.strike)][0 if k.contract_type == ContractType.CALL else 1] += k.open_interest
    for view in ("0dte", "tactical"):
        g = c.storage.get_latest_gamma_aggregate(symbol, view=view)
        items = sorted((i for i in g.items if abs(float(i.strike) - spot) <= half), key=lambda i: i.strike)
        nets = [float(i.call_gamma_exposure) + float(i.put_gamma_exposure) for i in items]
        flips = sum(1 for a, b in zip(nets, nets[1:]) if (a > 0) != (b > 0))
        print(f"\n{symbol} {view} spot {spot:.2f} exp0 {exp0} | strikes within +-{half}: {len(items)} | sign changes between neighbours: {flips}")
        if view == "0dte":
            print("   strike     call_gex      put_gex          net   callOI   putOI")
            for i, n in zip(items, nets):
                co, po = oi.get(float(i.strike), (0, 0))
                print(f"   {float(i.strike):7.1f} {float(i.call_gamma_exposure):12.3e} {float(i.put_gamma_exposure):12.3e} {n:12.3e} {co:8d} {po:7d} {'+' if n > 0 else '-'}")
