"""READ-ONLY: third mentor capture (2026-07-23, SPX 7413.26, 0DTE): OI calls vs puts by strike (tooltip: strike 7450 put OI 2.2K; put OI ~4.7-4.9K at 7300/7320/7350;
7500 total ~5.3K), Max Pain 7515, volatility smile (ATM ~13%, puts 24% at 7290, 32% at 7530, calls 23.5% at 7530). Prints OI and IV by strike from the stage-2 files at 12:45 and 13:45."""
import glob
import gzip
import json

D = r"V:\Convexa\hist\raw\stage2"
DAY = "2026-07-23"
f0 = oi_f = None
for p in glob.glob(D + r"\manifest_*tanda*.jsonl"):
    for line in open(p, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            if r.get("status") == "downloaded" and r.get("symbol") == "SPXW" and r.get("day") == DAY:
                if r["dataset"] == "greeks5m" and r["params"]["expiration"] == DAY:
                    f0 = r["file"]
                elif r["dataset"] == "oi":
                    oi_f = r["file"]


def load(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        body = json.loads(fh.read())
    return body["response"] if isinstance(body, dict) and "response" in body else body


oi = {}
for e in load(oi_f):
    c = e["contract"]
    if c["expiration"] == DAY:
        oi[(float(c["strike"]), c["right"])] = e["data"][0]["open_interest"] if e["data"] else 0
bars = {}
for e in load(f0):
    bars[(float(e["contract"]["strike"]), e["contract"]["right"])] = {b["timestamp"][11:16]: b for b in e["data"]}
strikes = sorted({k[0] for k in bars})
print("0DTE strikes in file:", len(strikes), strikes[0], strikes[-1])
print("OI (calls, puts) by strike, mentor chart range 7290-7530 step 10 (+7450, 7515):")
for k in [s for s in strikes if 7290 <= s <= 7530 and (s % 10 == 0 or s in (7515, 7525))]:
    print("  %d  calls %5d  puts %5d  total %5d" % (k, oi.get((k, "CALL"), 0), oi.get((k, "PUT"), 0), oi.get((k, "CALL"), 0) + oi.get((k, "PUT"), 0)))
for hm in ("12:45", "13:45"):
    spot = None
    for (k, r_), bb in bars.items():
        if hm in bb and bb[hm].get("underlying_price", 0) > 0:
            spot = bb[hm]["underlying_price"]
            break
    print("\nIV by strike at %s (spot %.2f), percent: strike: call / put" % (hm, spot))
    line = []
    for k in [s for s in strikes if 7290 <= s <= 7530 and s % 20 == 0]:
        c = bars.get((k, "CALL"), {}).get(hm, {}).get("implied_vol")
        p = bars.get((k, "PUT"), {}).get(hm, {}).get("implied_vol")
        line.append("%d: %s / %s" % (k, "-" if not c else "%.1f" % (100 * c), "-" if not p else "%.1f" % (100 * p)))
    print("  " + " | ".join(line))
