"""READ-ONLY. Why the regime (net gamma) changes between OI and Volumen on the 0DTE tab: per symbol, the stored net gamma of '0dte' and '0dte_volume', and how much of it
comes from calls and from puts above / below the spot (per-strike call and put exposure summed). Nothing is written."""
import sys

sys.path.insert(0, "V:/Convexa/repo")

from backend.core.container import build_container

c = build_container()
for symbol in ["SPX", "NDX", "SPY", "QQQ", "IWM"]:
    spot = None
    for view in ("0dte", "0dte_volume"):
        g = c.storage.get_latest_gamma_aggregate(symbol, view=view)
        if g is None:
            print(f"{symbol} {view}: missing")
            continue
        price = c.storage.get_latest_price(symbol)
        spot = float(price.price) if price else None
        parts = {"call_above": 0.0, "call_below": 0.0, "put_above": 0.0, "put_below": 0.0}
        for i in g.items:
            side = "above" if spot is not None and float(i.strike) >= spot else "below"
            parts[f"call_{side}"] += float(i.call_gamma_exposure)
            parts[f"put_{side}"] += float(i.put_gamma_exposure)
        print(
            f"{symbol:4s} {view:12s} net {float(g.net_gamma):+.3e} | per-strike sum {sum(parts.values()):+.3e} | "
            + " ".join(f"{k} {v:+.2e}" for k, v in parts.items())
        )
