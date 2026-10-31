"""READ-ONLY: backtest of Convexa's OWN expected move (backend.domain.use_cases.calculate_expected_move, the VIX-style variance calculation on the 0DTE chain) against what the
price did to the close, on the stage-2 files (SPXW/SPY/QQQ/NDXP 5-minute bars with bid/ask/IV, 60 sessions 2026-07-13 .. 2026-10-05). Samples every 30 minutes from ~10:00 to 15:00.
For each sample, three readings of the same calculation:
  A  |close - spot| <= implied_1sd_dollars (the 1-sigma move that the calculation computes with the time left in T, around spot)
  B  |close - spot| <= remaining_1sd_dollars (implied x sqrt(time_to_close_pct): scaled by the time left a second time)
  C  close inside [lower_bound, upper_bound] (implied_1sd around the session's first price, which is what the chart band uses)
A well calibrated 1-sigma reading holds ~68.3% (2 sigma 95.4%) and has z-std 1. Prints a summary and writes cone_expected_move.csv next to the other stage-2 working files.
No ThetaData call, no database access. Usage: python hm_expected_move_test.py [--symbols SPX,SPY,QQQ,NDX] [--days N]"""
from __future__ import annotations

import argparse
import csv
import glob
import gzip
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, time as tod
from decimal import Decimal
from zoneinfo import ZoneInfo

sys.path.insert(0, "V:/Convexa/repo")
from backend.domain.entities import ContractType, OptionChain, OptionContract, OptionGreeks
from backend.domain.use_cases.calculate_expected_move import calculate_expected_move

ET = ZoneInfo("America/New_York")
D = r"V:\Convexa\hist\raw\stage2"
OUT = r"V:\Convexa\bench\hm_stage2"
ROOT = {"SPX": "SPXW", "SPY": "SPY", "QQQ": "QQQ", "NDX": "NDXP"}
ARGS = argparse.ArgumentParser()
ARGS.add_argument("--symbols", default="SPX,SPY,QQQ,NDX")
ARGS.add_argument("--days", type=int, default=0)
OPT = ARGS.parse_args()


def load(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        body = json.loads(fh.read())
    return body["response"] if isinstance(body, dict) and "response" in body else body


greeks_files = defaultdict(list)
for p in glob.glob(D + r"\manifest_*tanda*.jsonl"):
    for line in open(p, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("status") == "downloaded" and r["dataset"] == "greeks5m":
            greeks_files[(r["symbol"], r["day"])].append((r["params"]["expiration"], r["file"]))

# regime (net gamma sign) of the same samples, from the earlier study (same stamps: every 6th bar)
regime = {}
gpath = os.path.join(OUT, "cone_gamma.csv")
if os.path.exists(gpath):
    for r in csv.DictReader(open(gpath, encoding="utf-8")):
        regime[(r["symbol"], r["day"], int(r["i"]))] = "long" if float(r["net_gamma"]) >= 0 else "short"

rows = []
for symbol in [s for s in OPT.symbols.split(",") if s in ROOT]:
    root = ROOT[symbol]
    days = sorted({d for (r_, d) in greeks_files if r_ == root})
    if OPT.days:
        days = days[-OPT.days:]
    for day_s in days:
        day = date.fromisoformat(day_s)
        zero = [f for exp, f in greeks_files[(root, day_s)] if exp == day_s]
        if not zero:
            continue
        entries = load(zero[0])
        bars = {}
        for e in entries:
            bars[(float(e["contract"]["strike"]), e["contract"]["right"])] = {b["timestamp"]: b for b in e["data"]}
        by_ts = defaultdict(list)
        for key, bb in list(bars.items())[:60]:
            for ts, b in bb.items():
                if b.get("underlying_price", 0) > 0:
                    by_ts[ts].append(b["underlying_price"])
        stamps = sorted(ts for ts in by_ts if len(by_ts[ts]) >= 5 and "09:30" <= ts[11:16] <= "16:00")
        if len(stamps) < 40:
            continue
        price = [sorted(by_ts[ts])[len(by_ts[ts]) // 2] for ts in stamps]
        n = len(stamps)
        close_price = price[n - 1]
        open_price = price[0]
        for i in range(6, n - 1, 6):
            ts = stamps[i]
            if ts[11:16] > "15:00":
                break
            when = datetime.combine(day, tod(int(ts[11:13]), int(ts[14:16])), tzinfo=ET)
            spot = Decimal(str(price[i]))
            contracts = []
            for (strike_f, right), bb in bars.items():
                b = bb.get(ts)
                if b is None or b.get("underlying_price", 0) <= 0:
                    continue
                bid, ask = Decimal(str(b["bid"])), Decimal(str(b["ask"]))
                if ask < bid or Decimal(str(b["implied_vol"])) < 0:
                    continue
                ctype = ContractType.CALL if right == "CALL" else ContractType.PUT
                contracts.append(OptionContract(underlying=symbol, strike=Decimal(str(strike_f)), expiration=day, contract_type=ctype, occ_symbol=f"{root}{day_s}{right[0]}{strike_f}",
                                                bid=bid, ask=ask, last=(bid + ask) / 2, volume=0, open_interest=0, iv=Decimal(str(b["implied_vol"])),
                                                greeks=OptionGreeks(delta=Decimal(str(b["delta"])), gamma=Decimal(0), theta=Decimal(str(b["theta"])), vega=Decimal(str(b["vega"])), charm=Decimal(0), vanna=Decimal(0))))
            if len(contracts) < 20:
                continue
            chain = OptionChain(symbol=symbol, as_of=when, spot_price=spot, contracts=tuple(contracts))
            em = calculate_expected_move(chain, when, session_open_price=Decimal(str(open_price)))
            move = close_price - price[i]
            implied, remaining = float(em.implied_1sd_dollars), float(em.remaining_1sd_dollars)
            rows.append(dict(symbol=symbol, day=day_s, i=i, time=ts[11:16], hour=int(ts[11:13]), regime=regime.get((symbol, day_s, i), ""), spot=price[i], atm_iv=float(em.atm_iv),
                             implied_1sd=implied, remaining_1sd=remaining, move_to_close=move, z_A=move / implied if implied else None, z_B=move / remaining if remaining else None,
                             in_C=int(float(em.lower_bound) <= close_price <= float(em.upper_bound)), mins_to_close=(16 * 60) - (int(ts[11:13]) * 60 + int(ts[14:16]))))
    print(symbol, "samples so far", len(rows), flush=True)

os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "cone_expected_move.csv"), "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)


def stats(sel, key):
    zs = [r[key] for r in sel if r[key] is not None]
    if not zs:
        return "n=0"
    sd = math.sqrt(sum(z * z for z in zs) / len(zs))
    k = statistics.median(abs(z) for z in zs) / 0.6745
    return "n=%d in1s %.1f%% in2s %.1f%% RMS z %.2f median-based scale %.2f" % (len(zs), 100 * sum(abs(z) <= 1 for z in zs) / len(zs), 100 * sum(abs(z) <= 2 for z in zs) / len(zs), sd, k)


print("\n=== Convexa expected move vs the move to the close (target: 68.3% / 95.4%, RMS z 1.00, scale 1.00) ===")
for label, sel in [("ALL", rows)] + [("symbol " + s, [r for r in rows if r["symbol"] == s]) for s in sorted({r["symbol"] for r in rows})]:
    print(label)
    print("  A (implied around spot):   ", stats(sel, "z_A"))
    print("  B (remaining = x sqrt t):  ", stats(sel, "z_B"))
    inc = [r["in_C"] for r in sel]
    print("  C (band around the open, as drawn on the chart): inside %.1f%% (n=%d)" % (100 * sum(inc) / len(inc), len(inc)))
print("\nBy hour (ALL symbols):")
for h in sorted({r["hour"] for r in rows}):
    sel = [r for r in rows if r["hour"] == h]
    print("  %02d:xx  A:" % h, stats(sel, "z_A"), "| B:", stats(sel, "z_B"))
print("\nBy regime (ALL symbols, samples with a stored regime):")
for g in ("long", "short"):
    sel = [r for r in rows if r["regime"] == g]
    print("  %-5s gamma  A:" % g, stats(sel, "z_A"), "| B:", stats(sel, "z_B"))
print("\nSPX only, by hour:")
for h in sorted({r["hour"] for r in rows if r["symbol"] == "SPX"}):
    sel = [r for r in rows if r["symbol"] == "SPX" and r["hour"] == h]
    print("  %02d:xx  A:" % h, stats(sel, "z_A"))
print("\nmedian atm_iv of the calculation %.1f%%, median implied 1sd %.2f%% of spot" % (100 * statistics.median(r["atm_iv"] for r in rows), 100 * statistics.median(r["implied_1sd"] / r["spot"] for r in rows)))
