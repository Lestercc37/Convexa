"""READ-ONLY: fourth mentor capture (2026-07-23, SPX exp 2026-07-23, 'Contract Side Statistics (Premium)'):
Volume premium puts $652.09M (55.1%) / calls $532.07M (44.9%), total $1.18B | OI premium puts $296.74M (93.8%) / calls $19.65M, total $316.39M |
Open interest puts 180.4K (52.5%) / calls 163.5K (47.5%), total 343.9K | Volume puts 1.05M (50.5%) / calls 1.03M (49.5%), total 2.07M.
Rebuilds open interest, OI premium (OI x 100 x mid) and, if the stage-2 bars carry it, volume, from the stage-2 SPXW files."""
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


oi = {"CALL": 0, "PUT": 0}
oi_by = {}
for e in load(oi_f):
    c = e["contract"]
    if c["expiration"] == DAY:
        v = e["data"][0]["open_interest"] if e["data"] else 0
        oi[c["right"]] += v
        oi_by[(float(c["strike"]), c["right"])] = v
tot = oi["CALL"] + oi["PUT"]
print("OI 0DTE all strikes: calls %d (%.1f%%) puts %d (%.1f%%) total %d | mentor: calls 163.5K (47.5%%) puts 180.4K (52.5%%) total 343.9K" % (oi["CALL"], 100 * oi["CALL"] / tot, oi["PUT"], 100 * oi["PUT"] / tot, tot))
entries = load(f0)
print("keys of one 5-min bar:", sorted(entries[0]["data"][0].keys()))
for hm in ("12:45", "13:45", "15:55"):
    stamp = f"{DAY}T{hm}:00.000"
    prem = {"CALL": 0.0, "PUT": 0.0}
    vol = {"CALL": 0.0, "PUT": 0.0}
    has_vol = False
    for e in entries:
        c = e["contract"]
        b = next((x for x in e["data"] if x["timestamp"] == stamp), None)
        if b is None:
            continue
        k = (float(c["strike"]), c["right"])
        mid = (b["bid"] + b["ask"]) / 2
        prem[c["right"]] += oi_by.get(k, 0) * 100 * mid
        if "volume" in b:
            has_vol = True
            vol[c["right"]] += b["volume"] or 0
    t = prem["CALL"] + prem["PUT"]
    print("%s OI premium (OI x 100 x mid): puts $%.2fM (%.1f%%) calls $%.2fM total $%.2fM | mentor: puts $296.74M (93.8%%) calls $19.65M total $316.39M" % (hm, prem["PUT"] / 1e6, 100 * prem["PUT"] / t, prem["CALL"] / 1e6, t / 1e6))
    if has_vol:
        print("   bar volume summed (this bar only, not cumulative): calls %d puts %d" % (vol["CALL"], vol["PUT"]))
