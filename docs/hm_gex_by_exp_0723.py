"""READ-ONLY: capture 8 (mentor, 2026-07-23 ~13:30, 'Gamma Exposure por Expiracion - SPX'): bars per expiration, call GEX / put GEX / net GEX in $B per 1% move.
Read from the picture (approximate): Jul 23 (0DTE) net ~-18.5, calls ~+2, puts ~-21 | Jul 24 (1d) net ~-14, calls ~+5.7, puts ~-19.8 | Jul 27 (4d) net ~-3.7, calls ~+1.7, puts ~-5.4.
Rebuilds the same per-expiration totals from the stage-2 SPXW files (the files hold only the 0..4-day expirations) with Convexa's calculators at 13:30 ET, strikes within +-10% of spot."""
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
from backend.adapters.providers.thetadata.provider import _build_occ_symbol, _time_to_expiration_years
from backend.domain.entities import ContractType, OptionChain, OptionContract, OptionGreeks
from backend.domain.use_cases.calculate_bsm_greeks import calculate_bsm_greeks

ET = ZoneInfo("America/New_York")
D = r"V:\Convexa\hist\raw\stage2"
DAY = "2026-07-23"
RATE = Decimal("0.043")
exp_calc, agg_calc = FakeGammaExposureCalculator(), FakeGammaAggregateCalculator()


def load(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        body = json.loads(fh.read())
    return body["response"] if isinstance(body, dict) and "response" in body else body


files, oi_f = {}, None
for p in glob.glob(D + r"\manifest_*tanda*.jsonl"):
    for line in open(p, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            if r.get("status") == "downloaded" and r.get("symbol") == "SPXW" and r.get("day") == DAY:
                if r["dataset"] == "greeks5m":
                    files[r["params"]["expiration"]] = r["file"]
                elif r["dataset"] == "oi":
                    oi_f = r["file"]
print("expirations in the stage-2 files for", DAY, sorted(files))
oi = defaultdict(int)
for e in load(oi_f):
    c = e["contract"]
    oi[(float(c["strike"]), c["right"], c["expiration"])] = e["data"][0]["open_interest"] if e["data"] else 0
day = date.fromisoformat(DAY)
for hm in ("13:30",):
    stamp = f"{DAY}T{hm}:00.000"
    when = datetime.combine(day, tod(int(hm[:2]), int(hm[3:])), tzinfo=ET)
    data = {exp: load(f) for exp, f in sorted(files.items())}
    spots = [b["underlying_price"] for e in data[DAY][:100] for b in e["data"] if b["timestamp"] == stamp and b.get("underlying_price", 0) > 0]
    spot = Decimal(str(statistics.median(spots)))
    print(hm, "spot", spot)
    for exp_s, entries in data.items():
        exp = date.fromisoformat(exp_s)
        contracts = []
        for e in entries:
            b = next((x for x in e["data"] if x["timestamp"] == stamp), None)
            if b is None or b.get("underlying_price", 0) <= 0:
                continue
            strike = Decimal(str(e["contract"]["strike"]))
            if abs(strike - spot) > spot * Decimal("0.10"):
                continue
            iv = Decimal(str(b["implied_vol"]))
            bsm = calculate_bsm_greeks(Decimal(str(b["underlying_price"])), strike, RATE, iv, _time_to_expiration_years(exp, when))
            ctype = ContractType.CALL if e["contract"]["right"] == "CALL" else ContractType.PUT
            bid, ask = Decimal(str(b["bid"])), Decimal(str(b["ask"]))
            contracts.append(OptionContract(underlying="SPX", strike=strike, expiration=exp, contract_type=ctype, occ_symbol=_build_occ_symbol("SPXW", exp, ctype, strike), bid=bid, ask=ask,
                                            last=(bid + ask) / 2, volume=0, open_interest=int(oi.get((float(e["contract"]["strike"]), e["contract"]["right"], exp_s), 0)), iv=iv,
                                            greeks=OptionGreeks(delta=Decimal(str(b["delta"])), gamma=bsm.gamma, theta=Decimal(str(b["theta"])), vega=Decimal(str(b["vega"])), charm=bsm.charm, vanna=bsm.vanna)))
        agg = agg_calc.calculate(exp_calc.calculate(OptionChain(symbol="SPX", as_of=when, spot_price=spot, contracts=tuple(contracts))), "SPX", when)
        c_ = float(sum((i.call_gamma_exposure for i in agg.items), Decimal(0))) / 1e9
        p_ = float(sum((i.put_gamma_exposure for i in agg.items), Decimal(0))) / 1e9
        print("  exp %s (%dd): call GEX %+.2f B | put GEX %+.2f B | net %+.2f B | contracts %d" % (exp_s, (exp - day).days, c_, p_, c_ + p_, len(contracts)))
