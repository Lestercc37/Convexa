"""READ-ONLY. SPY 0-2 DTE (tactical) by open interest: the latest cycle's top 6 strikes by |call|+|put| gamma exposure with the open interest behind each
(summed over the 0-2 DTE expirations), and how far behind the second strike is. Small reads."""
import sys

sys.path.insert(0, "V:/Convexa/repo")

from backend.core.container import build_container

c = build_container()
g = c.storage.get_latest_gamma_aggregate("SPY", view="tactical")
price = c.storage.get_latest_price("SPY")
print(f"SPY tactical {g.as_of:%H:%M} price {float(price.price):.2f} abs {float(g.absolute_gamma_strike):g}")
top = sorted(g.items, key=lambda i: abs(float(i.call_gamma_exposure)) + abs(float(i.put_gamma_exposure)), reverse=True)[:6]
first = abs(float(top[0].call_gamma_exposure)) + abs(float(top[0].put_gamma_exposure))
for i in top:
    v = abs(float(i.call_gamma_exposure)) + abs(float(i.put_gamma_exposure))
    print(f"  {float(i.strike):g}: |call|+|put| {v:.3e} ({100 * v / first:.0f} % of the first) open interest {i.open_interest}")
