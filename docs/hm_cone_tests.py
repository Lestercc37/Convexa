"""READ-ONLY study built from the stage-2 files (SPXW/SPY/QQQ/NDXP 5-minute greeks + OI, no ThetaData call, no database access).
Written 2026-10-17 to test the mentor platform's "Predictions" tab with Convexa data. Two CSVs under V:/Convexa/bench/hm_stage2:
  cone_bars.csv   every 5-min bar from the 12th on: price, realized sigma since the open, ATM IV (nearest expiration after today), forward returns at 5/15/60 min and to the close.
  cone_gamma.csv  every 30 minutes 10:00-15:00: net gamma (0-2 DTE chain, +-1.5% of spot, production calculators), gamma centroid, past 15/30-min return, forward returns 5/15/30 min and to the close.
Usage: python hm_cone_tests.py [--symbols SPX,SPY,QQQ,NDX] [--days N]"""
from __future__ import annotations

import argparse
import csv
import glob
import gzip
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, time as tod, timedelta
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
OUT = r"V:\Convexa\bench\hm_stage2"
ROOT = {"SPX": "SPXW", "SPY": "SPY", "QQQ": "QQQ", "NDX": "NDXP"}
RATE = Decimal("0.043")
ARGS = argparse.ArgumentParser()
ARGS.add_argument("--symbols", default="SPX,SPY,QQQ,NDX")
ARGS.add_argument("--days", type=int, default=0)
ARGS.add_argument("--zero-dte", action="store_true", help="gamma regime and centroid from the 0DTE expiration only (the mentor screen selects 0DTE); writes cone_gamma_0dte.csv and no bars file")
OPT = ARGS.parse_args()
exposure_calc, agg_calc = FakeGammaExposureCalculator(), FakeGammaAggregateCalculator()
ANNUAL_5M = 78 * 252  # five-minute periods per year of RTH sessions (= 98,280 minutes / 5)


def load(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        body = json.loads(fh.read())
    return body["response"] if isinstance(body, dict) and "response" in body else body


greeks_files = defaultdict(list)
oi_file = {}
for p in glob.glob(D + r"\manifest_*tanda*.jsonl"):
    for line in open(p, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("status") != "downloaded":
            continue
        if r["dataset"] == "greeks5m":
            greeks_files[(r["symbol"], r["day"])].append((r["params"]["expiration"], r["file"]))
        elif r["dataset"] == "oi":
            oi_file[(r["symbol"], r["day"])] = r["file"]

bars_rows, gamma_rows = [], []
t_start = time.time()
for symbol in [s for s in OPT.symbols.split(",") if s in ROOT]:
    root = ROOT[symbol]
    days = sorted({d for (r_, d) in greeks_files if r_ == root})
    if OPT.days:
        days = days[-OPT.days:]
    for day_s in days:
        day = date.fromisoformat(day_s)
        cutoff = day + timedelta(days=2)
        files = sorted(greeks_files[(root, day_s)])
        data = {}  # exp -> {(strike,right): {ts: bar}}
        for exp_s, f in files:
            exp = date.fromisoformat(exp_s)
            if not (day <= exp <= cutoff):
                continue
            idx = {}
            for e in load(f):
                idx[(float(e["contract"]["strike"]), e["contract"]["right"])] = {b["timestamp"]: b for b in e["data"]}
            data[exp] = idx
        if not data:
            continue
        oi = defaultdict(int)
        if (root, day_s) in oi_file:
            for e in load(oi_file[(root, day_s)]):
                c = e["contract"]
                oi[(float(c["strike"]), c["right"], date.fromisoformat(c["expiration"]))] = e["data"][0]["open_interest"] if e["data"] else 0
        first_exp = min(data)
        by_ts = defaultdict(list)
        for key, bars in list(data[first_exp].items())[:60]:
            for ts, b in bars.items():
                if b.get("underlying_price", 0) > 0:
                    by_ts[ts].append(b["underlying_price"])
        stamps = sorted(ts for ts in by_ts if len(by_ts[ts]) >= 5)
        stamps = [ts for ts in stamps if "09:30" <= ts[11:16] <= "16:00"]  # 16:00 bar = the close (2026-10-17 fix: it was cut at 15:55)
        if len(stamps) < 40:
            print(symbol, day_s, "too few bars", len(stamps))
            continue
        price = []
        for ts in stamps:
            v = sorted(by_ts[ts])
            price.append(v[len(v) // 2])
        n = len(stamps)
        rets = [0.0] + [math.log(price[j] / price[j - 1]) for j in range(1, n)]
        after = sorted(e for e in data if e > day)
        iv_exp = after[0] if after else first_exp

        def iv_ref(i):
            ts, spot = stamps[i], price[i]
            cands = sorted((abs(k[0] - spot), k) for k, bars in data[iv_exp].items() if ts in bars and bars[ts].get("implied_vol", 0) > 0)
            top = cands[:4]
            return sum(data[iv_exp][k][ts]["implied_vol"] for _, k in top) / len(top) if top else ""

        def mins_to_close(i):
            hh, mm = int(stamps[i][11:13]), int(stamps[i][14:16])
            return 16 * 60 - (hh * 60 + mm)

        for i in range(12, n):
            sq = sum(r * r for r in rets[1:i + 1]) / i
            row = dict(symbol=symbol, day=day_s, i=i, time=stamps[i][11:16], spot=price[i], sig_real=math.sqrt(sq * ANNUAL_5M), iv=iv_ref(i), mins_to_close=mins_to_close(i))
            for h in (1, 3, 12):
                row["ret%d" % h] = (price[i + h] / price[i] - 1) if i + h < n else ""
            row["retclose"] = price[n - 1] / price[i] - 1
            bars_rows.append(row)
        for i in range(6, n - 1, 6):
            if stamps[i][11:16] > "15:00":
                break
            spot = Decimal(str(price[i]))
            ts = stamps[i]
            when = datetime.combine(day, tod(int(ts[11:13]), int(ts[14:16])), tzinfo=ET)
            half = spot * Decimal("0.015")
            contracts = []
            for exp, idx in data.items():
                if OPT.zero_dte and exp != day:
                    continue
                tte = _time_to_expiration_years(exp, when)
                for (strike_f, right), bars in idx.items():
                    b = bars.get(ts)
                    if b is None or b.get("underlying_price", 0) <= 0:
                        continue
                    strike = Decimal(str(strike_f))
                    if abs(strike - spot) > half:
                        continue
                    iv = Decimal(str(b["implied_vol"]))
                    bsm = calculate_bsm_greeks(Decimal(str(b["underlying_price"])), strike, RATE, iv, tte)
                    ctype = ContractType.CALL if right == "CALL" else ContractType.PUT
                    bid, ask = Decimal(str(b["bid"])), Decimal(str(b["ask"]))
                    contracts.append(OptionContract(underlying=symbol, strike=strike, expiration=exp, contract_type=ctype, occ_symbol=_build_occ_symbol(root, exp, ctype, strike), bid=bid, ask=ask,
                                                    last=(bid + ask) / 2, volume=0, open_interest=int(oi.get((strike_f, right, exp), 0)), iv=iv,
                                                    greeks=OptionGreeks(delta=Decimal(str(b["delta"])), gamma=bsm.gamma, theta=Decimal(str(b["theta"])), vega=Decimal(str(b["vega"])), charm=bsm.charm, vanna=bsm.vanna)))
            if len(contracts) < 20:
                continue
            chain = OptionChain(symbol=symbol, as_of=when, spot_price=spot, contracts=tuple(contracts))
            agg = agg_calc.calculate(exposure_calc.calculate(chain), symbol, when)
            tot = sum(float(it.total_gamma_exposure) for it in agg.items)
            totn = sum(abs(float(it.net_gamma)) for it in agg.items)
            if tot <= 0:
                continue
            cen_total = sum(float(it.strike) * float(it.total_gamma_exposure) for it in agg.items) / tot
            cen_net = sum(float(it.strike) * abs(float(it.net_gamma)) for it in agg.items) / totn if totn > 0 else cen_total
            row = dict(symbol=symbol, day=day_s, i=i, time=ts[11:16], spot=price[i], net_gamma=float(agg.net_gamma), total_gamma=tot, centroid_total=cen_total, centroid_net=cen_net,
                       past3=(price[i] / price[i - 3] - 1), past6=(price[i] / price[i - 6] - 1), mins_to_close=mins_to_close(i),
                       sig_real=math.sqrt(sum(r * r for r in rets[1:i + 1]) / i * ANNUAL_5M) if i >= 12 else "")
            for h in (1, 3, 6):
                row["ret%d" % h] = (price[i + h] / price[i] - 1) if i + h < n else ""
            row["retclose"] = price[n - 1] / price[i] - 1
            gamma_rows.append(row)
        print(symbol, day_s, "bars", n, "first", stamps[0][11:16], "last", stamps[-1][11:16], "| rows so far bars %d gamma %d | %.0f s" % (len(bars_rows), len(gamma_rows), time.time() - t_start), flush=True)

os.makedirs(OUT, exist_ok=True)
for name, rows in (("cone_bars.csv", bars_rows), ("cone_gamma.csv", gamma_rows)):
    if OPT.zero_dte:
        if name == "cone_bars.csv":
            continue
        name = "cone_gamma_0dte.csv"
    if rows:
        keys = list(dict.fromkeys(k for r in rows for k in r))
        with open(os.path.join(OUT, name), "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
        print("wrote", os.path.join(OUT, name), len(rows))
