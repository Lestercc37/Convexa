"""READ-ONLY. Cost of the per-expiration gamma views (0DTE / 1DTE / 2DTE) vs the current 0-2 DTE tactical build, on each symbol's latest stored chain.
Nothing is saved: it calls the orchestrator's private _build_view directly (the same code the scheduler runs) and times it. Prints the listed expirations used, the contracts and strikes per view and the seconds."""
import sys
import time
from dataclasses import replace

sys.path.insert(0, "V:/Convexa/repo")

from backend.core.container import build_container
from backend.domain.use_cases.calculate_atr_range import REQUIRED_DAILY_BARS
from backend.domain.use_cases.gamma import TACTICAL_WINDOW_DAYS
from backend.domain.use_cases.market_hours import EASTERN_TIME

c = build_container()
orch = c.calculate_gamma_exposure_orchestrator
for symbol in ["SPX", "NDX", "SPY", "QQQ", "IWM", "NVDA", "VIX"]:
    t0 = time.perf_counter()
    chain = c.storage.get_latest_chain_snapshot(symbol)
    read_s = time.perf_counter() - t0
    bars = c.storage.get_daily_bars(symbol, REQUIRED_DAILY_BARS)
    today = chain.as_of.astimezone(EASTERN_TIME).date()
    listed = sorted({k.expiration for k in chain.contracts if k.expiration >= today})[:3]
    t0 = time.perf_counter()
    tactical = orch._build_view(symbol, chain, bars, TACTICAL_WINDOW_DAYS, today, "tactical")
    tac_s = time.perf_counter() - t0
    parts = [f"{symbol:5s} as_of {chain.as_of.astimezone(EASTERN_TIME):%m-%d %H:%M} | read {read_s:.2f}s | contracts {len(chain.contracts)} | 0-2 DTE {tac_s:.2f}s strikes {len(tactical.items)} flip {tactical.gamma_flip}"]
    for n, expiration in enumerate(listed):
        one = replace(chain, contracts=tuple(k for k in chain.contracts if k.expiration == expiration))
        t0 = time.perf_counter()
        view = orch._build_view(symbol, one, bars, 0, expiration, "tactical")
        s = time.perf_counter() - t0
        parts.append(f"   {n}DTE={expiration} contracts {len(one.contracts)} {s:.2f}s strikes {len(view.items)} cw {view.call_wall} pw {view.put_wall} flip {view.gamma_flip}")
    print("\n".join(parts))
