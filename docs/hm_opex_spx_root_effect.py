"""READ-ONLY: effect of the AM-settled SPX-root contracts (monthly, already settled at today's open) on SPX's Tactical levels on an OPEX day: compute the Tactical view from the latest stored chain with and without them."""
import re
import sys
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, "V:/Convexa/repo")
from backend.core.container import build_container
from backend.domain.use_cases.gamma import TACTICAL_WINDOW_DAYS, REQUIRED_DAILY_BARS
ET = ZoneInfo("America/New_York")
c = build_container()
orch = c.calculate_gamma_exposure_orchestrator
for sym in ("SPX", "NDX", "VIX"):
    chain = c.storage.get_latest_chain_snapshot(sym)
    today = chain.as_of.astimezone(ET).date()
    roots = {}
    for k in chain.contracts:
        m = re.match(r"^([A-Z]+)\d{6}[CP]\d{8}$", k.occ_symbol)
        if k.expiration == today:
            roots.setdefault(m.group(1) if m else "?", []).append(k)
    print(sym, "chain as_of", chain.as_of.astimezone(ET).strftime("%H:%M:%S"), "| contracts expiring today by root:", {r: (len(v), sum(x.open_interest for x in v)) for r, v in roots.items()})
    if len(roots) < 2:
        continue
    am_root = sym  # the AM-settled root carries the index symbol itself (SPX, NDX, VIX)
    bars = c.storage.get_daily_bars(sym, REQUIRED_DAILY_BARS)
    anchor = today
    full = orch._build_view(sym, chain, bars, TACTICAL_WINDOW_DAYS, anchor, "tactical")
    kept = tuple(k for k in chain.contracts if not (k.expiration == today and re.match(r"^%s\d" % am_root, k.occ_symbol)))
    trimmed = orch._build_view(sym, replace(chain, contracts=kept), bars, TACTICAL_WINDOW_DAYS, anchor, "tactical")
    for name, g in (("with the AM root", full), ("without it", trimmed)):
        print("  %-18s call wall %s | put wall %s | flip %s | ABS %s | net GEX %.3g" % (name, g.call_wall, g.put_wall, None if g.gamma_flip is None else round(float(g.gamma_flip), 1), g.absolute_gamma_strike, float(g.net_gamma)))
