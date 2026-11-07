"""Analysis of flow_bars.csv written by hm_flow_lead_test.py (stdlib only): py -3 hm_flow_lead_analysis.py <dir with flow_bars.csv>
Spearman rank correlation with ranks computed WITHIN each day (so quiet and busy days weigh alike), pooled; 95% interval by bootstrap over DAYS (all symbols of a day together, 2000 draws).
Also: hit rate of the flow sign on the strongest 20% of bars of each day, and the partial correlation after removing what the previous 5-minute return already explains."""
import csv
import math
import random
import sys
from collections import defaultdict

path = (sys.argv[1] if len(sys.argv) > 1 else ".") + "/flow_bars.csv"
rows = list(csv.DictReader(open(path, encoding="utf-8")))
FLOWS = [("delta_simple_15", "PRIMARY: delta flow, simple conditions, last 15 min"), ("delta_simple_5", "delta flow, simple, this 5-min bar"), ("delta_all_15", "delta flow, all signed prints, last 15 min"),
         ("prem_simple_15", "premium flow, simple, last 15 min"), ("prem_all_15", "premium flow, all signed prints, last 15 min"), ("prem_all_5", "premium flow, all, this 5-min bar")]
TARGETS = [("r_same", "same bar (does flow FOLLOW price?)"), ("r_next5", "next 5 min"), ("r_next15", "next 15 min")]


def f(x):
    return float(x) if x not in ("", None) else None


def ranks(vals):
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    out = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2.0
        i = j + 1
    n = len(vals)
    return [x / (n - 1) - 0.5 if n > 1 else 0.0 for x in out]


def corr(a, b):
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((x - mb) ** 2 for x in b))
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb) if sa > 0 and sb > 0 else None


def pooled(sel, fk, tk, partial=False):
    """pooled Spearman over days: (flow rank, target rank) pairs, ranks inside each (symbol, day)."""
    xs, ys, day_of = [], [], []
    groups = defaultdict(list)
    for r in sel:
        x, y = f(r[fk]), f(r[tk])
        if x is None or y is None or (partial and f(r["r_prev5"]) is None):
            continue
        groups[(r["symbol"], r["day"])].append(r)
    for (sym, day), rs in groups.items():
        x = [f(r[fk]) for r in rs]
        y = [f(r[tk]) for r in rs]
        if partial:
            p = [f(r["r_prev5"]) for r in rs]
            ry, rp = ranks(y), ranks(p)
            # residual of the target rank on the previous-return rank (simple OLS inside the day)
            mp, my = sum(rp) / len(rp), sum(ry) / len(ry)
            var = sum((a - mp) ** 2 for a in rp) or 1.0
            beta = sum((a - mp) * (b - my) for a, b in zip(rp, ry)) / var
            y = [b - beta * (a - mp) for a, b in zip(rp, ry)]
            rx = ranks(x)
            mx = sum(rx) / len(rx)
            bx = sum((a - mp) * (b - mx) for a, b in zip(rp, rx)) / var
            x = [b - bx * (a - mp) for a, b in zip(rp, rx)]
            rxs, rys = x, y
        else:
            rxs, rys = ranks(x), ranks(y)
        xs += rxs
        ys += rys
        day_of += [(sym, day)] * len(rs)
    return xs, ys, day_of


def boot(xs, ys, day_of, reps=1000):
    by = defaultdict(list)
    for i, d in enumerate(day_of):
        by[d[1]].append(i)
    days = list(by)
    rng = random.Random(11)
    out = []
    for _ in range(reps):
        idx = []
        for _ in days:
            idx += by[rng.choice(days)]
        c = corr([xs[i] for i in idx], [ys[i] for i in idx])
        if c is not None:
            out.append(c)
    out.sort()
    return out[int(0.025 * len(out))], out[int(0.975 * len(out))]


print("bars", len(rows), "| symbol-days", len({(r['symbol'], r['day']) for r in rows}), "| days", len({r['day'] for r in rows}))
for sym_label, sel in (("SPX (SPXW 0DTE)", [r for r in rows if r["symbol"] == "SPXW"]), ("SPY 0DTE", [r for r in rows if r["symbol"] == "SPY"]), ("BOTH", rows)):
    print("\n=====", sym_label, "=====")
    for fk, flab in FLOWS:
        line = "%-62s" % flab
        for tk, tlab in TARGETS:
            xs, ys, d = pooled(sel, fk, tk)
            c = corr(xs, ys)
            lo, hi = boot(xs, ys, d, 600)
            line += " | %-9s %+.3f [%+.3f,%+.3f]" % (tk.replace("r_", ""), c, lo, hi)
        print(line)
print("\n=== PARTIAL (after removing what the previous 5-minute return explains), next 15 min ===")
for sym_label, sel in (("SPX", [r for r in rows if r["symbol"] == "SPXW"]), ("SPY", [r for r in rows if r["symbol"] == "SPY"])):
    for fk in ("delta_simple_15", "delta_all_15", "prem_all_15"):
        xs, ys, d = pooled(sel, fk, "r_next15", partial=True)
        lo, hi = boot(xs, ys, d, 600)
        print("%-4s %-18s partial corr %+.3f [%+.3f, %+.3f] (n=%d)" % (sym_label, fk, corr(xs, ys), lo, hi, len(xs)))
print("\n=== HIT RATE of the flow sign on the strongest 20% of bars of each day (delta flow, simple, last 15 min) vs the NEXT 15 min ===")
for sym_label, sel in (("SPX", [r for r in rows if r["symbol"] == "SPXW"]), ("SPY", [r for r in rows if r["symbol"] == "SPY"])):
    hits = tot = up = 0
    groups = defaultdict(list)
    for r in sel:
        if f(r["delta_simple_15"]) is not None and f(r["r_next15"]) not in (None, 0.0):
            groups[(r["symbol"], r["day"])].append(r)
    for rs in groups.values():
        cut = sorted(abs(f(r["delta_simple_15"])) for r in rs)[int(0.8 * (len(rs) - 1))]
        for r in rs:
            x, y = f(r["delta_simple_15"]), f(r["r_next15"])
            if abs(x) >= cut and x != 0:
                tot += 1
                hits += (x > 0) == (y > 0)
                up += y > 0
    print("%-4s strongest-20%% bars n=%d | flow sign right %.1f%% | share of up moves in the same bars %.1f%%" % (sym_label, tot, 100 * hits / tot, 100 * up / tot))
    hits = tot = 0
    for rs in groups.values():
        for r in rs:
            x, y = f(r["r_prev5"]), f(r["r_next15"])
            if x and y:
                tot += 1
                hits += (x > 0) == (y > 0)
    print("%-4s control: sign of the previous 5-minute RETURN right %.1f%% (n=%d)" % (sym_label, 100 * hits / tot, tot))
