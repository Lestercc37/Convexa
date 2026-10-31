"""READ-ONLY: capture 10 (mentor 'SPX - GEX Heatmap', strike x expiration net GEX in $M, spot 7,407.02, 2026-07-23). For many 5-minute stamps rebuilds net GEX per (strike, expiration)
for the cells below with Convexa's calculators and prints which stamp reproduces the picture best (median absolute log ratio) plus the best stamp's cell values."""
import glob
import gzip
import json
import math
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
THEIRS = {  # (expiration, strike): net GEX in $M as read from the picture
    ("2026-07-23", 7125): -10.51, ("2026-07-23", 7130): -1.09, ("2026-07-23", 7135): -12.66, ("2026-07-23", 7140): -11.10, ("2026-07-23", 7150): -47.16,
    ("2026-07-23", 7175): -69.21, ("2026-07-23", 7180): -15.33,
    ("2026-07-24", 7125): -14.21, ("2026-07-24", 7130): -7.11, ("2026-07-24", 7135): -6.64, ("2026-07-24", 7140): -6.76, ("2026-07-24", 7150): -49.52,
    ("2026-07-24", 7160): -28.11, ("2026-07-24", 7170): -12.68, ("2026-07-24", 7175): -12.45, ("2026-07-24", 7180): -12.97,
    ("2026-07-27", 7125): -12.69, ("2026-07-27", 7130): -71.90, ("2026-07-27", 7150): -28.42, ("2026-07-27", 7160): -11.68, ("2026-07-27", 7170): -10.61,
    ("2026-07-27", 7175): -65.85, ("2026-07-27", 7180): -8.24,
}
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
oi = defaultdict(int)
for e in load(oi_f):
    c = e["contract"]
    oi[(float(c["strike"]), c["right"], c["expiration"])] = e["data"][0]["open_interest"] if e["data"] else 0
day = date.fromisoformat(DAY)
strikes_needed = {k for _, k in THEIRS}
data = {}
for exp_s, f in files.items():
    data[exp_s] = {(float(e["contract"]["strike"]), e["contract"]["right"]): {b["timestamp"][11:16]: b for b in e["data"]} for e in load(f) if int(float(e["contract"]["strike"])) in strikes_needed}
spot_path = {}
first = load(files[DAY])
by_ts = defaultdict(list)
for e in first[:100]:
    for b in e["data"]:
        if b.get("underlying_price", 0) > 0:
            by_ts[b["timestamp"][11:16]].append(b["underlying_price"])
spot_path = {k: statistics.median(v) for k, v in by_ts.items() if len(v) >= 5}
results = []
by_col = []
for hm in sorted(spot_path):
    if not ("09:40" <= hm <= "15:55"):
        continue
    when = datetime.combine(day, tod(int(hm[:2]), int(hm[3:])), tzinfo=ET)
    spot = Decimal(str(spot_path[hm]))
    cells = {}
    for exp_s, idx in data.items():
        exp = date.fromisoformat(exp_s)
        for k in strikes_needed:
            contracts = []
            for right in ("CALL", "PUT"):
                b = idx.get((float(k), right), {}).get(hm)
                if b is None or b.get("underlying_price", 0) <= 0:
                    continue
                strike = Decimal(k)
                iv = Decimal(str(b["implied_vol"]))
                bsm = calculate_bsm_greeks(Decimal(str(b["underlying_price"])), strike, RATE, iv, _time_to_expiration_years(exp, when))
                ctype = ContractType.CALL if right == "CALL" else ContractType.PUT
                bid, ask = Decimal(str(b["bid"])), Decimal(str(b["ask"]))
                contracts.append(OptionContract(underlying="SPX", strike=strike, expiration=exp, contract_type=ctype, occ_symbol=_build_occ_symbol("SPXW", exp, ctype, strike), bid=bid, ask=ask,
                                                last=(bid + ask) / 2, volume=0, open_interest=int(oi.get((float(k), right, exp_s), 0)), iv=iv,
                                                greeks=OptionGreeks(delta=Decimal(str(b["delta"])), gamma=bsm.gamma, theta=Decimal(str(b["theta"])), vega=Decimal(str(b["vega"])), charm=bsm.charm, vanna=bsm.vanna)))
            if contracts:
                agg = agg_calc.calculate(exp_calc.calculate(OptionChain(symbol="SPX", as_of=when, spot_price=spot, contracts=tuple(contracts))), "SPX", when)
                cells[(exp_s, k)] = float(agg.net_gamma) / 1e6
    errs = [abs(math.log(abs(cells[c]) / abs(v))) for c, v in THEIRS.items() if c in cells and cells[c] != 0]
    if errs:
        results.append((statistics.median(errs), hm, float(spot), cells))
    e0 = [abs(math.log(abs(cells[c]) / abs(v))) for c, v in THEIRS.items() if c[0] == DAY and c in cells and cells[c] != 0]
    e1 = [abs(math.log(abs(cells[c]) / abs(v))) for c, v in THEIRS.items() if c[0] != DAY and c in cells and cells[c] != 0]
    if e0 and e1:
        by_col.append((statistics.median(e0), statistics.median(e1), hm, float(spot)))
results.sort()
print("0DTE-column cells only, best stamps (median |log ratio|, 1D/4D cells at the same stamp, spot):")
for a, b, hm, sp in sorted(by_col)[:6]:
    print("  %s spot %.2f | 0DTE cells %.3f | 1D/4D cells %.3f" % (hm, sp, a, b))
print("1D/4D cells only, best stamps:")
for a, b, hm, sp in sorted(by_col, key=lambda x: x[1])[:4]:
    print("  %s spot %.2f | 1D/4D cells %.3f | 0DTE cells %.3f" % (hm, sp, b, a))

print("best stamps (median |log ratio| over %d cells; 0.00 = identical, 0.10 = 10%% off):" % len(THEIRS))
for err, hm, sp, cells in results[:6]:
    print("  %s spot %.2f | median |log ratio| %.3f" % (hm, sp, err))
err, hm, sp, cells = next(r for r in results if r[1] == "10:50")
print("\nbest stamp %s spot %.2f; cells (mentor -> Convexa, $M):" % (hm, sp))
for c, v in THEIRS.items():
    print("  %s %d: %8.2f -> %8.2f" % (c[0][5:], c[1], v, cells.get(c, float("nan"))))
print("\nsame cells at the stamps closest to the mentor's spot 7407.02:", sorted(((abs(s - 7407.02), h, round(s, 2)) for h, s in spot_path.items()))[:4])
