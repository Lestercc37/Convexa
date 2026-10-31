"""Analysis of the two CSVs written by hm_cone_tests.py (stdlib only; run with `py -3 hm_cone_analysis.py <dir with cone_bars.csv and cone_gamma.csv>`).

Test 1 - volatility cone calibration: z = forward return / (sigma * sqrt(minutes / 98,280)); a good cone has ~68.3% of |z| <= 1 and ~95.4% <= 2.
         sigma variants: realized since the open, ATM IV (nearest expiration after today), the mentor's blend 0.5 realized + 0.5 IV.
Test 2 - direction rule (the mentor's, with the flow step replaced by a price-momentum proxy because the 5-minute files hold no trades):
         net gamma >= 0 (long gamma): predict the move toward the gamma centroid; net gamma < 0 (short gamma): predict continuation of the past 15-minute move.
         Pre-registered primary cells: pooled symbols, 5 and 15 minutes, both regimes (4 cells). 'Signal' only if hit rate >= 55% AND the day-clustered 95% interval stays above 50%.
Hit rate with Wilson interval, plus a bootstrap over DAYS (all symbols of a day together) because SPX/SPY/QQQ/NDX move together."""
import csv
import math
import random
import sys
from collections import defaultdict

d = sys.argv[1] if len(sys.argv) > 1 else "."
bars = list(csv.DictReader(open(d + "/cone_bars.csv", encoding="utf-8")))
gam = list(csv.DictReader(open(d + "/cone_gamma.csv", encoding="utf-8")))
MIN_YEAR = 98280.0


def minutes(hm):
    return int(hm[:2]) * 60 + int(hm[3:5])


def f(x):
    return float(x) if x not in ("", None) else None


print("bars rows", len(bars), "| gamma rows", len(gam), "| days", len({r["day"] for r in bars}), "| symbols", sorted({r["symbol"] for r in bars}))

print("\n=== TEST 1: volatility cone calibration (share of forward moves inside 1 and 2 sigma; target 68.3% / 95.4%; z std target 1.00) ===")
HOR = (("5 min", "ret1", 5), ("15 min", "ret3", 15), ("60 min", "ret12", 60))
for sig_name in ("real", "iv", "blend"):
    print("sigma =", sig_name)
    for sym in ["ALL"] + sorted({r["symbol"] for r in bars}):
        line = "  %-4s" % sym
        for hname, col, mins in HOR:
            zs = []
            for r in bars:
                if sym != "ALL" and r["symbol"] != sym:
                    continue
                ret, sr, iv = f(r[col]), f(r["sig_real"]), f(r["iv"])
                if ret is None or sr is None or sr <= 0:
                    continue
                if sig_name == "iv":
                    if not iv:
                        continue
                    s = iv
                elif sig_name == "blend":
                    if not iv:
                        continue
                    s = 0.5 * sr + 0.5 * iv
                else:
                    s = sr
                zs.append(ret / (s * math.sqrt(mins / MIN_YEAR)))
            if zs:
                m = sum(zs) / len(zs)
                sd = math.sqrt(sum((z - m) ** 2 for z in zs) / len(zs))
                line += " | %s n=%d in1s %.1f%% in2s %.1f%% zstd %.2f" % (hname, len(zs), 100 * sum(abs(z) <= 1 for z in zs) / len(zs), 100 * sum(abs(z) <= 2 for z in zs) / len(zs), sd)
        print(line)
for sig_name in ("blend",):
    line = "  to the close (samples at 11:00 and 13:00), sigma=blend:"
    zs = []
    for r in bars:
        if minutes(r["time"]) not in range(minutes("11:00") - 5, minutes("11:00") + 6) and minutes(r["time"]) not in range(minutes("13:00") - 5, minutes("13:00") + 6):
            continue
        ret, sr, iv, mt = f(r["retclose"]), f(r["sig_real"]), f(r["iv"]), f(r["mins_to_close"])
        if ret is None or not sr or not iv or not mt:
            continue
        zs.append(ret / ((0.5 * sr + 0.5 * iv) * math.sqrt(mt / MIN_YEAR)))
    if zs:
        m = sum(zs) / len(zs)
        sd = math.sqrt(sum((z - m) ** 2 for z in zs) / len(zs))
        print(line, "n=%d in1s %.1f%% in2s %.1f%% zstd %.2f mean z %.2f" % (len(zs), 100 * sum(abs(z) <= 1 for z in zs) / len(zs), 100 * sum(abs(z) <= 2 for z in zs) / len(zs), sd, m))


def wilson(h, n, z=1.96):
    if n == 0:
        return (0, 0)
    p = h / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    w = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return c - w, c + w


def sgn(x):
    return 1 if x > 0 else (-1 if x < 0 else 0)


def rule_pred(r, rule):
    sp = f(r["spot"])
    ng = f(r["net_gamma"])
    cen = f(r["centroid_total"]) if "total" in rule else f(r["centroid_net"])
    mom = f(r["past3"]) if "m3" in rule else f(r["past6"])
    long_g = ng >= 0
    if rule.startswith("A"):          # mentor-like: long -> centroid, short -> momentum
        return sgn(cen - sp) if long_g else sgn(mom)
    if rule.startswith("centroid"):   # centroid pull in every regime (control)
        return sgn(cen - sp)
    if rule.startswith("mom"):        # momentum in every regime (control)
        return sgn(mom)
    if rule.startswith("rev"):        # reversal of the past move in every regime (control)
        return -sgn(mom)
    return 0


def minutes(hm):
    return int(hm[:2]) * 60 + int(hm[3:5])


def cell(rows, rule, col, regime=None, time_only=None):
    out = []
    if time_only:  # one row per symbol and day: the sample closest to that time of day (file stamps differ by 5 minutes between months)
        best = {}
        for r in rows:
            k = (r["symbol"], r["day"])
            dist = abs(minutes(r["time"]) - minutes(time_only))
            if dist <= 5 and (k not in best or dist < best[k][0]):
                best[k] = (dist, r)
        rows = [v[1] for v in best.values()]
    for r in rows:
        ng = f(r["net_gamma"])
        if regime == "long" and ng < 0:
            continue
        if regime == "short" and ng >= 0:
            continue
        ret = f(r[col])
        if ret is None or ret == 0:
            continue
        p = rule_pred(r, rule)
        if p == 0:
            continue
        out.append((r["day"], 1 if p == sgn(ret) else 0, 1 if ret > 0 else 0))
    return out


def summarize(label, res, boot=False):
    n = len(res)
    if n == 0:
        print("  %-52s n=0" % label)
        return
    h = sum(x[1] for x in res)
    up = sum(x[2] for x in res)
    lo, hi = wilson(h, n)
    txt = "  %-52s n=%4d hit %.1f%% (Wilson %.1f-%.1f) | share of up moves in the same rows %.1f%%" % (label, n, 100 * h / n, 100 * lo, 100 * hi, 100 * up / n)
    if boot:
        bd = defaultdict(list)
        for x in res:
            bd[x[0]].append(x)
        days = list(bd)
        rng = random.Random(7)
        rates = []
        for _ in range(2000):
            tot = hit = 0
            for _ in days:
                for x in bd[rng.choice(days)]:
                    tot += 1
                    hit += x[1]
            rates.append(hit / tot)
        rates.sort()
        txt += " | by-day bootstrap %.1f-%.1f (%d days)" % (100 * rates[50], 100 * rates[1949], len(days))
    print(txt)


print("\n=== TEST 2: direction (sample every 30 min 10:00-15:00) ===")
syms = sorted({r["symbol"] for r in gam})
print("regime counts (net gamma >= 0 / < 0):", {s: (sum(1 for r in gam if r["symbol"] == s and f(r["net_gamma"]) >= 0), sum(1 for r in gam if r["symbol"] == s and f(r["net_gamma"]) < 0)) for s in syms})
print("\nPRIMARY (pre-registered): rule A (centroid by total gamma; momentum = past 15 min), pooled symbols")
for col, name in (("ret1", "5 min"), ("ret3", "15 min")):
    for regime in ("long", "short"):
        summarize("%s | %s gamma | rule A" % (name, regime), cell(gam, "A_total_m3", col, regime), boot=True)
print("\nSECONDARY: other horizons and variants, pooled")
for col, name in (("ret6", "30 min"),):
    for regime in ("long", "short"):
        summarize("%s | %s gamma | rule A" % (name, regime), cell(gam, "A_total_m3", col, regime), boot=True)
for regime in ("long", "short"):
    summarize("to the close from 11:00 | %s gamma | rule A" % regime, cell(gam, "A_total_m3", "retclose", regime, time_only="11:00"), boot=True)
for rule in ("A_net_m3", "A_total_m6"):
    for regime in ("long", "short"):
        summarize("15 min | %s gamma | rule %s" % (regime, rule), cell(gam, rule, "ret3", regime))
print("\nCONTROLS at 15 min (does the regime matter?)")
for rule in ("centroid_total", "mom_m3", "rev_m3"):
    for regime in ("long", "short"):
        summarize("15 min | %s gamma | control %s" % (regime, rule), cell(gam, rule, "ret3", regime))
print("\nBY SYMBOL, rule A, 15 min")
for s in syms:
    rows = [r for r in gam if r["symbol"] == s]
    for regime in ("long", "short"):
        summarize("%s | %s gamma" % (s, regime), cell(rows, "A_total_m3", "ret3", regime))
print("\nSTRENGTH (long gamma only): centroid farther than 0.10% of spot from price vs closer, 15 min")
far, near = [], []
for r in gam:
    if f(r["net_gamma"]) < 0:
        continue
    dist = abs(f(r["centroid_total"]) - f(r["spot"])) / f(r["spot"])
    (far if dist > 0.001 else near).append(r)
summarize("far (> 0.10%%), n rows %d" % len(far), cell(far, "A_total_m3", "ret3", "long"))
summarize("near (<= 0.10%%), n rows %d" % len(near), cell(near, "A_total_m3", "ret3", "long"))
