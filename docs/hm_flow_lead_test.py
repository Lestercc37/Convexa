"""READ-ONLY test (2026-10-18, Lester: "does having signed option flow in real time make any difference?"): does the SIGNED option flow of one 5-minute bar say anything about what the underlying does NEXT?
Data: stage-3 trade_quote files (0DTE contracts, every print with the quote before it) of SPXW and SPY, one file per 15-minute window of each session (~60 sessions 2026-07-13 .. 2026-10-16), and the stage-2 5-minute
greeks files (underlying price path and |delta| per contract; sessions without them are skipped). No ThetaData call, no database access.
Signing (per print): price >= ask -> buyer-initiated (+1); price <= bid -> seller-initiated (-1); inside the spread -> not signed (skipped); cancels (conditions 40-44) skipped.
Direction: buy call / sell put = +1 (bullish), sell call / buy put = -1 (bearish).
Four flow series per 5-minute bar: premium flow (sum dir x price x size x 100) and delta flow (sum dir x size x |delta|), each for ALL signed prints and for the SIMPLE single-leg conditions only (18, 95, 125, 126).
Bar k covers [t, t+5 min); the flow is known at t+5. Targets: same-bar return P(t+5)/P(t)-1 (flow follows price?), next 5 min and next 15 min (does it lead?). Flow of the last three bars (15 min) too.
PRE-REGISTERED PRIMARY CELL: delta flow, simple conditions, last 15 min, vs the NEXT 15 minutes, Spearman rank correlation (ranks within each day), pooled, 95% interval by bootstrap over days;
'signal' only if the correlation is >= 0.05 AND the interval stays above 0, in both SPX and SPY. Everything else is secondary (many comparisons: nothing is declared from a single cell).
Usage: python hm_flow_lead_test.py [--days N] [--workers K]   (low priority, K processes)"""
from __future__ import annotations

import argparse
import csv
import glob
import gzip
import json
import math
import os
import random
import re
import statistics
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

OUT = r"V:\Convexa\bench\hm_stage2"
S3 = {"SPXW": "V:/Convexa/hist/raw/stage3_A/SPXW", "SPY": "V:/Convexa/hist/raw/stage3_A/SPY"}
S3_EXTRA = {"SPXW": ["V:/Convexa/hist/raw/stage3_test/SPXW", "V:/Convexa/hist/raw/stage3_1016/SPXW"], "SPY": ["V:/Convexa/hist/raw/stage3_test/SPY", "V:/Convexa/hist/raw/stage3_1016/SPY"]}
S2 = r"V:\Convexa\hist\raw\stage2"
SIMPLE = {18, 95, 125, 126}
CANCELS = set(range(40, 45))
WIN = re.compile(r"_(\d{4})-(\d{4})(?:_h[12])?\.ndjson\.gz$")


def load_json(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        body = json.loads(fh.read())
    return body["response"] if isinstance(body, dict) and "response" in body else body


def day_files(root, day):
    files = []
    for base in [S3[root]] + S3_EXTRA[root]:
        for p in glob.glob(f"{base}/{day}/tq_{root}_exp{day}_*.ndjson.gz"):
            n = os.path.basename(p)
            if "_pre" in n or "_eve" in n or not WIN.search(n):
                continue
            files.append(p)
    return sorted(set(files))


def process(task):
    root, day, greeks_path = task
    entries = load_json(greeks_path)
    delta = {}
    by_ts = defaultdict(list)
    for e in entries:
        c = e["contract"]
        k = (round(float(c["strike"]), 2), c["right"][0])
        for b in e["data"]:
            hh, mm = int(b["timestamp"][11:13]), int(b["timestamp"][14:16])
            delta[(k, hh * 60 + mm)] = abs(float(b.get("delta") or 0.0))
    for e in entries[:60]:
        for b in e["data"]:
            if b.get("underlying_price", 0) > 0:
                by_ts[int(b["timestamp"][11:13]) * 60 + int(b["timestamp"][14:16])].append(b["underlying_price"])
    price = {m: statistics.median(v) for m, v in by_ts.items() if len(v) >= 5}
    bars = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0, 0])  # prem_all, prem_simple, delta_all, delta_simple, signed prints, unsigned prints
    for path in day_files(root, day):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.startswith("{"):
                    continue
                r = json.loads(line)
                try:
                    cond = int(r.get("condition") or 0)
                except (TypeError, ValueError):
                    cond = 0
                if cond in CANCELS:
                    continue
                ts = r["trade_timestamp"]
                minute = int(ts[11:13]) * 60 + int(ts[14:16])
                bar = minute - minute % 5
                p, bid, ask, size = float(r["price"]), float(r["bid"]), float(r["ask"]), int(r["size"])
                b = bars[bar]
                if ask <= 0 or p <= 0:
                    b[5] += 1
                    continue
                if p >= ask:
                    side = 1
                elif p <= bid:
                    side = -1
                else:
                    b[5] += 1
                    continue
                direction = side if r["right"][0] in "Cc" else -side
                key = (round(float(r["strike"]), 2), r["right"][0].upper())
                d = delta.get((key, bar), delta.get((key, bar - 5), delta.get((key, bar + 5))))
                prem = direction * p * size * 100.0
                dflow = direction * size * d if d is not None else 0.0
                b[0] += prem
                b[2] += dflow
                if cond in SIMPLE:
                    b[1] += prem
                    b[3] += dflow
                b[4] += 1
    return root, day, dict(bars), price


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    opt = ap.parse_args()
    greeks = {}
    for p in glob.glob(S2 + r"\manifest_*tanda*.jsonl"):
        for line in open(p, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                if r.get("status") == "downloaded" and r["dataset"] == "greeks5m" and r["params"]["expiration"] == r["day"]:
                    greeks[(r["symbol"], r["day"])] = r["file"]
    tasks = []
    for root in ("SPXW", "SPY"):
        days = sorted(d for (s, d) in greeks if s == root and day_files(root, d))
        if opt.days:
            days = days[-opt.days:]
        tasks += [(root, d, greeks[(root, d)]) for d in days]
    print("tasks (symbol-days):", len(tasks), flush=True)
    try:
        import psutil  # lower priority so the services are not disturbed
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass
    t0 = time.time()
    results = []
    with ProcessPoolExecutor(max_workers=opt.workers) as pool:
        for i, res in enumerate(pool.map(process, tasks), 1):
            results.append(res)
            if i % 10 == 0:
                print("done %d/%d | %.0f s" % (i, len(tasks), time.time() - t0), flush=True)
    rows = []
    for root, day, bars, price in results:
        for start in range(9 * 60 + 35, 15 * 60 + 25, 5):
            end = start + 5
            if not all(m in price for m in (start, end, end + 5, end + 15)):
                continue
            def flow(i, k, kb):
                return sum(bars.get(start - 5 * j, [0.0] * 6)[k] for j in range(kb))
            row = dict(symbol=root, day=day, start=start,
                       r_same=price[end] / price[start] - 1, r_next5=price[end + 5] / price[end] - 1, r_next15=price[end + 15] / price[end] - 1,
                       r_prev5=price[start] / price[start - 5] - 1 if (start - 5) in price else None)
            for name, k in (("prem_all", 0), ("prem_simple", 1), ("delta_all", 2), ("delta_simple", 3)):
                row[name + "_5"] = bars.get(start, [0.0] * 6)[k]
                row[name + "_15"] = sum(bars.get(start - 5 * j, [0.0] * 6)[k] for j in range(3))
            rows.append(row)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "flow_bars.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("wrote flow_bars.csv", len(rows), "bars |", len({(r['symbol'], r['day']) for r in rows}), "symbol-days |", "%.0f s" % (time.time() - t0))


if __name__ == "__main__":
    main()
