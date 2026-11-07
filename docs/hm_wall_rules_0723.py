"""READ-ONLY: wall rules test on both 07-23 captures (their put wall 7400 / call wall 7450 / MVS 7430 at ~12:45, 7400/7450/7400 at 13:45); second mentor capture (2026-07-23, SPX 7413.69, 0DTE: net GEX -17.70B, total gamma 23.12B, net DEX -27.43B, net vanna +15.14B, flip 7529, max pain 7515, put wall 7400,
call wall 7450, P/C 1.10, MVS 7430, IV 13.7%; DEX chart: biggest put bars at 7500 (-2.25B), 7520 (-1.8B), 7430 (-1.5B), 7450 (-1.45B), 7550 (-1.4B)).
Finds the 5-minute stamps where SPX was near 7413.69 and rebuilds levels and per-strike delta exposure there with Convexa's calculators. No ThetaData call, no database access."""
from __future__ import annotations

import glob
import gzip
import json
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, time as tod
from decimal import Decimal
from zoneinfo import ZoneInfo

sys.path.insert(0, "V:/Convexa/repo")
from backend.adapters.providers.mock.gamma_aggregate import FakeGammaAggregateCalculator
from backend.adapters.providers.mock.gamma_exposure import FakeGammaExposureCalculator
from backend.adapters.providers.mock.gamma_flip import FakeGammaFlipCalculator
from backend.adapters.providers.mock.walls import FakeWallCalculator
from backend.adapters.providers.thetadata.provider import _build_occ_symbol, _time_to_expiration_years
from backend.domain.entities import ContractType, OptionChain, OptionContract, OptionGreeks
from backend.domain.use_cases.calculate_bsm_greeks import calculate_bsm_greeks

ET = ZoneInfo("America/New_York")
D = r"V:\Convexa\hist\raw\stage2"
DAY = "2026-07-23"
TARGET = 7413.69
RATE = Decimal("0.043")
exposure_calc, agg_calc = FakeGammaExposureCalculator(), FakeGammaAggregateCalculator()


def load(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        body = json.loads(fh.read())
    return body["response"] if isinstance(body, dict) and "response" in body else body


f0, oi_file = None, None
for p in glob.glob(D + r"\manifest_*tanda*.jsonl"):
    for line in open(p, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            if r.get("status") == "downloaded" and r.get("symbol") == "SPXW" and r.get("day") == DAY:
                if r["dataset"] == "greeks5m" and r["params"]["expiration"] == DAY:
                    f0 = r["file"]
                elif r["dataset"] == "oi":
                    oi_file = r["file"]
entries = load(f0)
oi = defaultdict(int)
for e in load(oi_file):
    c = e["contract"]
    oi[(float(c["strike"]), c["right"], date.fromisoformat(c["expiration"]))] = e["data"][0]["open_interest"] if e["data"] else 0
day = date.fromisoformat(DAY)
bars = {}
for e in entries:
    bars[(float(e["contract"]["strike"]), e["contract"]["right"])] = {b["timestamp"]: b for b in e["data"]}
path = defaultdict(list)
for e in entries[:120]:
    for b in e["data"]:
        if b.get("underlying_price", 0) > 0:
            path[b["timestamp"][11:16]].append(b["underlying_price"])
pts = {k: statistics.median(v) for k, v in path.items() if len(v) >= 5}
near = sorted((abs(v - TARGET), k, v) for k, v in pts.items())[:8]
print("stamps closest to %.2f:" % TARGET, [(k, round(v, 2)) for _, k, v in sorted(near, key=lambda x: x[1])])
print("all 12:00-14:00:", {k: round(v, 2) for k, v in sorted(pts.items()) if "12:00" <= k <= "14:00"})


for hm in ("12:45", "13:25", "13:45"):
    hh, mm = int(hm[:2]), int(hm[3:])
    stamp = f"{DAY}T{hm}:00.000"
    when = datetime.combine(day, tod(hh, mm), tzinfo=ET)
    spot = Decimal(str(pts[hm]))
    contracts = []
    for (strike_f, right), bb in bars.items():
        b = bb.get(stamp)
        if b is None or b.get("underlying_price", 0) <= 0:
            continue
        strike = Decimal(str(strike_f))
        if abs(strike - spot) > 400:
            continue
        iv = Decimal(str(b["implied_vol"]))
        bsm = calculate_bsm_greeks(Decimal(str(b["underlying_price"])), strike, RATE, iv, _time_to_expiration_years(day, when))
        ctype = ContractType.CALL if right == "CALL" else ContractType.PUT
        bid, ask = Decimal(str(b["bid"])), Decimal(str(b["ask"]))
        contracts.append(OptionContract(underlying="SPX", strike=strike, expiration=day, contract_type=ctype, occ_symbol=_build_occ_symbol("SPXW", day, ctype, strike), bid=bid, ask=ask,
                                        last=(bid + ask) / 2, volume=0, open_interest=int(oi.get((strike_f, right, day), 0)), iv=iv,
                                        greeks=OptionGreeks(delta=Decimal(str(b["delta"])), gamma=bsm.gamma, theta=Decimal(str(b["theta"])), vega=Decimal(str(b["vega"])), charm=bsm.charm, vanna=bsm.vanna)))
    chain = OptionChain(symbol="SPX", as_of=when, spot_price=spot, contracts=tuple(contracts))
    agg = agg_calc.calculate(exposure_calc.calculate(chain), "SPX", when)
    sp = float(spot)
    items = [(float(i.strike), float(i.call_gamma_exposure), float(i.put_gamma_exposure), float(i.net_gamma), float(i.total_gamma_exposure), i.open_interest) for i in agg.items]
    coi = defaultdict(int); poi = defaultdict(int)
    for c in contracts:
        (coi if c.contract_type == ContractType.CALL else poi)[float(c.strike)] += c.open_interest
    def top(lst, key, n=4):
        return [(int(k[0]), round(key(k) / 1e9, 2) if abs(key(k)) > 1e6 else key(k)) for k in sorted(lst, key=lambda k: -abs(key(k)))[:n]]
    print("\n%s spot %.2f (mentor: put wall 7400, call wall 7450)" % (hm, sp))
    print("  call gamma, strikes >= spot, top (B):", top([i for i in items if i[0] >= sp], lambda k: k[1]))
    print("  put gamma, strikes <= spot, top (B):", top([i for i in items if i[0] <= sp], lambda k: k[2]))
    print("  call gamma, any strike, top (B):", top(items, lambda k: k[1]))
    print("  put gamma, any strike, top (B):", top(items, lambda k: k[2]))
    print("  call OI >= spot top:", sorted(((k, v) for k, v in coi.items() if k >= sp), key=lambda x: -x[1])[:4], "| put OI <= spot top:", sorted(((k, v) for k, v in poi.items() if k <= sp), key=lambda x: -x[1])[:4])
    print("  ABS gamma strike (|C|+|P| max):", int(agg.absolute_gamma_strike), "| nearest-to-spot 25 strikes only, top call gamma:", top([i for i in items if abs(i[0] - sp) <= 100 and i[0] >= sp], lambda k: k[1], 2), "top put gamma:", top([i for i in items if abs(i[0] - sp) <= 100 and i[0] <= sp], lambda k: k[2], 2))
