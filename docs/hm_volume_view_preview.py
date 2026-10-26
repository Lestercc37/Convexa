"""READ-ONLY. Preview of the 0DTE volume-weighted view (OI replaced by today's traded volume, same _build_view) on each symbol's latest stored chain, next to the OI one:
levels and the sign of the net per strike near spot (+/-), to compare with GEXBot's "GEX (Volume)". Nothing is saved."""
import sys
from dataclasses import replace

sys.path.insert(0, "V:/Convexa/repo")

from backend.core.container import build_container
from backend.domain.use_cases.calculate_atr_range import REQUIRED_DAILY_BARS
from backend.domain.use_cases.gamma import listed_expiration_for_view

c = build_container()
orch = c.calculate_gamma_exposure_orchestrator
for symbol in ["SPX", "SPY", "QQQ", "NDX", "IWM"]:
    chain = c.storage.get_latest_chain_snapshot(symbol)
    bars = c.storage.get_daily_bars(symbol, REQUIRED_DAILY_BARS)
    exp = listed_expiration_for_view(chain, "0dte")
    one = [k for k in chain.contracts if k.expiration == exp]
    spot = float(chain.spot_price)
    for label, contracts in (("OI ", one), ("VOL", [replace(k, open_interest=k.volume) for k in one])):
        g = orch._build_view(symbol, replace(chain, contracts=tuple(contracts)), bars, 0, exp, "0dte")
        items = sorted(g.items, key=lambda i: i.strike)
        signs = "".join("+" if float(i.call_gamma_exposure) + float(i.put_gamma_exposure) > 0 else "-" for i in items)
        below = [i for i in items if float(i.strike) < spot]
        flips = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
        print(f"{symbol} {label} spot {spot:.2f} cw {g.call_wall} pw {g.put_wall} flip {float(g.gamma_flip or 0):.2f} abs {g.absolute_gamma_strike} | sign changes {flips} | {signs[:len(below)]}|{signs[len(below):]}")
