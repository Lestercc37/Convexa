"""WRITES gamma_aggregates / items (the same rows a scheduler cycle writes). Runs the orchestrator's execute_both once per tactical symbol on its LATEST STORED chain, so the new
0DTE / 1DTE / 2DTE views exist right after the deploy (the scheduler only writes them on its next cycle). The tactical row it rewrites has the same time and the same
content (upsert on underlying, time, view). Prints, per symbol and view, the stored strikes and levels."""
import sys

sys.path.insert(0, "V:/Convexa/repo")

from backend.core.container import build_container

c = build_container()
orch = c.calculate_gamma_exposure_orchestrator
for symbol in ["SPX", "NDX", "SPY", "QQQ", "IWM", "NVDA", "VIX"]:
    try:
        orch.execute_both(symbol)
    except Exception as error:  # one symbol never stops the others
        print(f"{symbol}: FAILED {type(error).__name__}: {str(error)[:120]}")
        continue
    for view in ("tactical", "0dte", "1dte", "2dte"):
        g = c.storage.get_latest_gamma_aggregate(symbol, view=view)
        if g is None:
            print(f"{symbol:5s} {view:8s} missing")
            continue
        print(f"{symbol:5s} {view:8s} {g.as_of:%m-%d %H:%M} strikes {len(g.items):3d} cw {g.call_wall} pw {g.put_wall} abs {g.absolute_gamma_strike}")
