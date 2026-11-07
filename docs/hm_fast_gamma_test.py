"""READ-ONLY offline test of a "fast layer": recompute the per-strike gamma between full chain cycles with the live spot, keeping the OI and IV of the last full cycle.
Idea (2026-10-17, Lester: "2 minutes is an eternity in scalping"): OI does not change during the day, so what moves 0DTE gamma between cycles is mostly the spot (and the clock).
For each sample (hourly, 0-2 DTE chain, strikes within +-1.5% of spot, production calculators) and horizons of 1 and 2 five-minute bars ahead (5 and 10 minutes, longer than the real gap of ~1 minute):
  ACTUAL = full chain at the later stamp (its own IV, spot, time)     FAST = IV/OI/quotes of the earlier stamp, gamma re-evaluated at the later spot and the later clock
  STALE  = the earlier snapshot left as it is (what the chart shows today until the next cycle)
Compares net GEX, gamma flip, absolute gamma strike and the whole per-strike profile. Stage-2 files only, no ThetaData call, no database access.
Usage: python hm_fast_gamma_test.py [--symbols SPX,SPY,QQQ,NDX] [--days N]"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, time as tod, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

sys.path.insert(0, "V:/Convexa/repo")
from backend.adapters.providers.mock.gamma_aggregate import FakeGammaAggregateCalculator
from backend.adapters.providers.mock.gamma_exposure import FakeGammaExposureCalculator
from backend.adapters.providers.mock.gamma_flip import FakeGammaFlipCalculator
from backend.adapters.providers.thetadata.provider import _build_occ_symbol, _time_to_expiration_years
from backend.domain.entities import ContractType, OptionChain, OptionContract, OptionGreeks
from backend.domain.use_cases.calculate_bsm_greeks import calculate_bsm_greeks

ET = ZoneInfo("America/New_York")
D = r"V:\Convexa\hist\raw\stage2"
ROOT = {"SPX": "SPXW", "SPY": "SPY", "QQQ": "QQQ", "NDX": "NDXP"}
RATE = Decimal("0.043")
ARGS = argparse.ArgumentParser()
ARGS.add_argument("--symbols", default="SPX,SPY,QQQ,NDX")
ARGS.add_argument("--days", type=int, default=0)
OPT = ARGS.parse_args()
exposure_calc, agg_calc, flip_calc = FakeGammaExposureCalculator(), FakeGammaAggregateCalculator(), FakeGammaFlipCalculator()


def load(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        body = json.loads(fh.read())
    return body["response"] if isinstance(body, dict) and "response" in body else body


greeks_files, oi_file = defaultdict(list), {}
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


def aggregate(symbol, root, day, data, oi, quote_ts, spot, when):
    """Full production aggregate: quotes/IV/OI from `quote_ts`, gamma evaluated at `spot` and the clock `when`."""
    contracts = []
    half = spot * Decimal("0.015")
    for exp, idx in data.items():
        tte = _time_to_expiration_years(exp, when)
        for (strike_f, right), bars in idx.items():
            b = bars.get(quote_ts)
            if b is None or b.get("underlying_price", 0) <= 0:
                continue
            strike = Decimal(str(strike_f))
            if abs(strike - spot) > half:
                continue
            iv = Decimal(str(b["implied_vol"]))
            bsm = calculate_bsm_greeks(spot, strike, RATE, iv, tte)
            ctype = ContractType.CALL if right == "CALL" else ContractType.PUT
            bid, ask = Decimal(str(b["bid"])), Decimal(str(b["ask"]))
            if ask < bid:  # a crossed quote in the file: the aggregate does not use the quote, but the contract entity refuses it
                bid = ask = (bid + ask) / 2
            contracts.append(OptionContract(underlying=symbol, strike=strike, expiration=exp, contract_type=ctype, occ_symbol=_build_occ_symbol(root, exp, ctype, strike), bid=bid, ask=ask,
                                            last=(bid + ask) / 2, volume=0, open_interest=int(oi.get((strike_f, right, exp), 0)), iv=iv,
                                            greeks=OptionGreeks(delta=Decimal(str(b["delta"])), gamma=bsm.gamma, theta=Decimal(str(b["theta"])), vega=Decimal(str(b["vega"])), charm=bsm.charm, vanna=bsm.vanna)))
    if len(contracts) < 20:
        return None
    agg = agg_calc.calculate(exposure_calc.calculate(OptionChain(symbol=symbol, as_of=when, spot_price=spot, contracts=tuple(contracts))), symbol, when)
    flip = flip_calc.calculate(agg, spot)
    return dict(net=float(agg.net_gamma), abs_strike=float(agg.absolute_gamma_strike), flip=float(flip.gamma_flip_price) if flip.flip_found else None,
                profile={float(i.strike): float(i.net_gamma) for i in agg.items})


def l1(a, b):
    keys = set(a) | set(b)
    den = sum(abs(a.get(k, 0.0)) for k in keys)
    return sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys) / den if den else None


rows = []
for symbol in [s for s in OPT.symbols.split(",") if s in ROOT]:
    root = ROOT[symbol]
    days = sorted({d for (r_, d) in greeks_files if r_ == root})
    if OPT.days:
        days = days[-OPT.days:]
    for day_s in days:
        day = date.fromisoformat(day_s)
        data = {}
        for exp_s, f in sorted(greeks_files[(root, day_s)]):
            exp = date.fromisoformat(exp_s)
            if day <= exp <= day + timedelta(days=2):
                data[exp] = {(float(e["contract"]["strike"]), e["contract"]["right"]): {b["timestamp"]: b for b in e["data"]} for e in load(f)}
        if not data:
            continue
        oi = defaultdict(int)
        if (root, day_s) in oi_file:
            for e in load(oi_file[(root, day_s)]):
                c = e["contract"]
                oi[(float(c["strike"]), c["right"], date.fromisoformat(c["expiration"]))] = e["data"][0]["open_interest"] if e["data"] else 0
        by_ts = defaultdict(list)
        for key, bars in list(data[min(data)].items())[:60]:
            for ts, b in bars.items():
                if b.get("underlying_price", 0) > 0:
                    by_ts[ts].append(b["underlying_price"])
        stamps = sorted(ts for ts in by_ts if len(by_ts[ts]) >= 5 and "09:30" <= ts[11:16] <= "16:00")
        if len(stamps) < 40:
            continue
        price = [Decimal(str(sorted(by_ts[ts])[len(by_ts[ts]) // 2])) for ts in stamps]

        def clock(i):
            return datetime.combine(day, tod(int(stamps[i][11:13]), int(stamps[i][14:16])), tzinfo=ET)

        for i in range(6, len(stamps) - 3, 12):
            if stamps[i][11:16] > "15:00":
                break
            stale = aggregate(symbol, root, day, data, oi, stamps[i], price[i], clock(i))
            if stale is None:
                continue
            for k in (1, 2):
                actual = aggregate(symbol, root, day, data, oi, stamps[i + k], price[i + k], clock(i + k))
                fast = aggregate(symbol, root, day, data, oi, stamps[i], price[i + k], clock(i + k))
                if actual is None or fast is None or actual["net"] == 0:
                    continue
                rows.append(dict(symbol=symbol, k=k, move=float(price[i + k] - price[i]),
                                 net_err_fast=abs(fast["net"] - actual["net"]) / abs(actual["net"]), net_err_stale=abs(stale["net"] - actual["net"]) / abs(actual["net"]),
                                 flip_err_fast=None if None in (fast["flip"], actual["flip"]) else abs(fast["flip"] - actual["flip"]),
                                 flip_err_stale=None if None in (stale["flip"], actual["flip"]) else abs(stale["flip"] - actual["flip"]),
                                 abs_ok_fast=int(fast["abs_strike"] == actual["abs_strike"]), abs_ok_stale=int(stale["abs_strike"] == actual["abs_strike"]),
                                 l1_fast=l1(fast["profile"], actual["profile"]), l1_stale=l1(stale["profile"], actual["profile"])))
    print(symbol, "samples so far", len(rows), flush=True)


def med(sel, key):
    v = [r[key] for r in sel if r[key] is not None]
    return statistics.median(v) if v else float("nan")


def p90(sel, key):
    v = sorted(r[key] for r in sel if r[key] is not None)
    return v[int(0.9 * (len(v) - 1))] if v else float("nan")


print("\n=== Fast layer (live spot + clock, OI/IV of the last cycle) vs leaving the last snapshot as it is; both compared with the full chain at the later stamp ===")
for label, sel0 in [("ALL", rows)] + [(s, [r for r in rows if r["symbol"] == s]) for s in sorted({r["symbol"] for r in rows})]:
    for k in (1, 2):
        sel = [r for r in sel0 if r["k"] == k]
        if not sel:
            continue
        better_net = sum(r["net_err_fast"] < r["net_err_stale"] for r in sel) / len(sel)
        better_l1 = sum((r["l1_fast"] or 9) < (r["l1_stale"] or 9) for r in sel) / len(sel)
        print("%-4s +%2d min (n=%d) | net GEX rel. error median fast %.1f%% vs stale %.1f%% (p90 %.1f%% vs %.1f%%) | flip error pts median %.1f vs %.1f (p90 %.1f vs %.1f) | ABS strike right %.0f%% vs %.0f%% | per-strike profile error median %.1f%% vs %.1f%% | fast better in net %.0f%%, in profile %.0f%%" % (
            label, 5 * k, len(sel), 100 * med(sel, "net_err_fast"), 100 * med(sel, "net_err_stale"), 100 * p90(sel, "net_err_fast"), 100 * p90(sel, "net_err_stale"),
            med(sel, "flip_err_fast"), med(sel, "flip_err_stale"), p90(sel, "flip_err_fast"), p90(sel, "flip_err_stale"),
            100 * sum(r["abs_ok_fast"] for r in sel) / len(sel), 100 * sum(r["abs_ok_stale"] for r in sel) / len(sel),
            100 * med(sel, "l1_fast"), 100 * med(sel, "l1_stale"), 100 * better_net, 100 * better_l1))
