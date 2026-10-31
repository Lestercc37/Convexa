"""READ-ONLY: rebuild SPX on 2026-07-23 around 13:30-13:40 ET from the stage-2 files (SPXW greeks5m + OI) with Convexa's own production calculators,
to compare with the mentor's platform screenshot (SPX 7401.58, 0DTE: net GEX -18.24B, total gamma 22.81B, net DEX -30.94B, net vanna +15.46B, flip 7529, max pain 7515,
put wall 7400, call wall 7450, P/C OI 1.10, ATM IV 14.2%). No ThetaData call, no database access. Prints levels and totals only."""
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
RATE = Decimal("0.043")
exposure_calc, agg_calc = FakeGammaExposureCalculator(), FakeGammaAggregateCalculator()


def load(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        body = json.loads(fh.read())
    return body["response"] if isinstance(body, dict) and "response" in body else body


greeks_files, oi_file = [], None
for p in glob.glob(D + r"\manifest_*tanda*.jsonl"):
    for line in open(p, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("status") != "downloaded" or r.get("symbol") != "SPXW" or r.get("day") != DAY:
            continue
        if r["dataset"] == "greeks5m":
            greeks_files.append((r["params"]["expiration"], r["file"]))
        elif r["dataset"] == "oi":
            oi_file = r["file"]
print("greeks5m expirations on", DAY, len(greeks_files), sorted(e for e, _ in greeks_files)[:6], "| oi file", bool(oi_file))
oi = defaultdict(int)
for e in load(oi_file):
    c = e["contract"]
    oi[(float(c["strike"]), c["right"], date.fromisoformat(c["expiration"]))] = e["data"][0]["open_interest"] if e["data"] else 0
day = date.fromisoformat(DAY)
cache = {exp: load(f) for exp, f in sorted(greeks_files) if day <= date.fromisoformat(exp) <= date.fromisoformat("2026-07-25")}
print("expirations loaded", sorted(cache))


def chain_at(stamp_hm, only_zero_dte, half_width):
    hh, mm = stamp_hm
    stamp = f"{DAY}T{hh:02d}:{mm:02d}:00.000"
    when = datetime.combine(day, tod(hh, mm), tzinfo=ET)
    rows = []
    for exp_s, entries in cache.items():
        exp = date.fromisoformat(exp_s)
        if only_zero_dte and exp != day:
            continue
        for e in entries:
            bar = next((b for b in e["data"] if b["timestamp"] == stamp), None)
            if bar and bar.get("underlying_price", 0) > 0:
                rows.append((e["contract"], exp, bar))
    if not rows:
        return None
    spot = Decimal(str(statistics.median(b["underlying_price"] for _, _, b in rows)))
    contracts = []
    for c, exp, b in rows:
        strike = Decimal(str(c["strike"]))
        if abs(strike - spot) > half_width:
            continue
        iv = Decimal(str(b["implied_vol"]))
        bsm = calculate_bsm_greeks(Decimal(str(b["underlying_price"])), strike, RATE, iv, _time_to_expiration_years(exp, when))
        ctype = ContractType.CALL if c["right"] == "CALL" else ContractType.PUT
        bid, ask = Decimal(str(b["bid"])), Decimal(str(b["ask"]))
        contracts.append(OptionContract(underlying="SPX", strike=strike, expiration=exp, contract_type=ctype, occ_symbol=_build_occ_symbol("SPXW", exp, ctype, strike), bid=bid, ask=ask,
                                        last=(bid + ask) / 2, volume=0, open_interest=int(oi.get((float(c["strike"]), c["right"], exp), 0)), iv=iv,
                                        greeks=OptionGreeks(delta=Decimal(str(b["delta"])), gamma=bsm.gamma, theta=Decimal(str(b["theta"])), vega=Decimal(str(b["vega"])), charm=bsm.charm, vanna=bsm.vanna)))
    return OptionChain(symbol="SPX", as_of=when, spot_price=spot, contracts=tuple(contracts)), rows


def max_pain(contracts):
    strikes = sorted({float(c.strike) for c in contracts})
    best = None
    for k in strikes:
        pain = sum(c.open_interest * (max(k - float(c.strike), 0) if c.contract_type == ContractType.CALL else max(float(c.strike) - k, 0)) for c in contracts)
        if best is None or pain < best[1]:
            best = (k, pain)
    return best[0] if best else None


for label, only0, hw in (("0DTE only, +-400 pts", True, Decimal(400)), ("0-2 DTE (Convexa tactical rule), +-400 pts", False, Decimal(400))):
    print("\n===", label)
    for hm in ((13, 40), (13, 45), (13, 50)):
        built = chain_at(hm, only0, hw)
        if built is None:
            print(hm, "no bars"); continue
        chain, rows = built
        exposures = exposure_calc.calculate(chain)
        agg = agg_calc.calculate(exposures, "SPX", chain.as_of)
        flip = FakeGammaFlipCalculator().calculate(agg, chain.spot_price)
        walls = FakeWallCalculator().calculate(agg)
        calls = sum(c.open_interest for c in chain.contracts if c.contract_type == ContractType.CALL)
        puts = sum(c.open_interest for c in chain.contracts if c.contract_type == ContractType.PUT)
        near = sorted(chain.contracts, key=lambda c: abs(c.strike - chain.spot_price))[:8]
        atm_iv = sum(float(c.iv) for c in near) / len(near)
        pos = float(sum((i.call_gamma_exposure for i in agg.items), Decimal(0)))
        neg = float(sum((i.put_gamma_exposure for i in agg.items), Decimal(0)))
        print("%02d:%02d spot %.2f | net GEX %.2f B | total (|C|+|P|) %.2f B (C %.2f, P %.2f) | net DEX %.2f B | net vanna %.2f B | flip %s | max pain %s | put wall %s | call wall %s | ABS %s | P/C OI %.2f | ATM IV %.1f%% | contracts %d" % (
            hm[0], hm[1], chain.spot_price, float(agg.net_gamma) / 1e9, (abs(pos) + abs(neg)) / 1e9, pos / 1e9, neg / 1e9,
            float(sum((e.delta_exposure for e in exposures), Decimal(0))) / 1e9, float(sum((e.vanna_exposure for e in exposures), Decimal(0))) / 1e9,
            None if not flip.flip_found else "%.1f" % flip.gamma_flip_price, max_pain(chain.contracts), None if walls.put_wall is None else walls.put_wall.strike,
            None if walls.call_wall is None else walls.call_wall.strike, agg.absolute_gamma_strike, puts / calls if calls else 0, 100 * atm_iv, len(chain.contracts)))

# ---- price path of the day (5-minute underlying price of the 0DTE file) and the stored daily bar
path = {}
for e in cache[DAY][:80]:
    for b in e["data"]:
        if b.get("underlying_price", 0) > 0:
            path.setdefault(b["timestamp"], []).append(b["underlying_price"])
pts = {k[11:16]: statistics.median(v) for k, v in path.items() if len(v) >= 5}
ks = sorted(pts)
print("\nSPX 5-min path 07-23: first bar", ks[0], "%.2f" % pts[ks[0]], "| last bar", ks[-1], "%.2f" % pts[ks[-1]], "| high %.2f at %s | low %.2f at %s" % (
    max(pts.values()), max(pts, key=pts.get), min(pts.values()), min(pts, key=pts.get)))
print("selected:", {k: round(pts[k], 2) for k in ("09:30", "09:35", "10:00", "11:00", "12:00", "13:00", "13:30", "13:40", "13:45", "13:50", "14:00", "15:00", "15:55") if k in pts})
from backend.core.container import build_container
c = build_container()
for b in c.storage.get_daily_bars("SPX", 400):
    if str(b.date) == DAY:
        print("stored daily bar SPX 07-23: open %s high %s low %s close %s" % (b.open_price, b.high, b.low, b.close))
