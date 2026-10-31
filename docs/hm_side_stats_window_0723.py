"""READ-ONLY: which strike window [L, U] around spot reproduces the mentor's Contract Side Statistics (07-23, 0DTE) at ~12:45:
OI puts 180.4K, OI calls 163.5K, OI premium puts $296.74M, calls $19.65M. Scans L and U and prints the best fits."""
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
entries = load(f0)
for hm in ("12:45",):
    stamp = f"{DAY}T{hm}:00.000"
    mid = {}
    for e in entries:
        b = next((x for x in e["data"] if x["timestamp"] == stamp), None)
        if b:
            mid[(float(e["contract"]["strike"]), e["contract"]["right"])] = (b["bid"] + b["ask"]) / 2
    strikes = sorted({k[0] for k in oi})
    target = dict(OIP=180400, OIC=163500, PRP=296.74e6, PRC=19.65e6)
    res = []
    for L in strikes:
        if L > 7400:
            break
        for U in strikes:
            if U < 7430:
                continue
            v = dict(OIP=0, OIC=0, PRP=0.0, PRC=0.0)
            for (k, right), n in oi.items():
                if L <= k <= U:
                    side = "P" if right == "PUT" else "C"
                    v["OI" + side] += n
                    v["PR" + side] += n * 100 * mid.get((k, right), 0)
            err = sum(abs(v[x] - target[x]) / target[x] for x in target)
            res.append((err, L, U, v))
    res.sort(key=lambda r: r[0])
    for err, L, U, v in res[:6]:
        print("L %.0f U %.0f | err %.3f | OI puts %d calls %d | premium puts $%.2fM calls $%.2fM" % (L, U, err, v["OIP"], v["OIC"], v["PRP"] / 1e6, v["PRC"] / 1e6))
    print("targets: OI puts 180400 calls 163500, premium puts $296.74M calls $19.65M; spot at 12:45 ~7414.49")
    for L, U in ((7414.49 * 0.9, 7414.49 * 1.1), (7414.49 * 0.95, 7414.49 * 1.05), (7414.49 - 400, 7414.49 + 400)):
        v = dict(OIP=0, OIC=0, PRP=0.0, PRC=0.0)
        for (k, right), n in oi.items():
            if L <= k <= U:
                side = "P" if right == "PUT" else "C"
                v["OI" + side] += n
                v["PR" + side] += n * 100 * mid.get((k, right), 0)
        print("window %.0f-%.0f: OI puts %d calls %d | premium puts $%.2fM calls $%.2fM" % (L, U, v["OIP"], v["OIC"], v["PRP"] / 1e6, v["PRC"] / 1e6))
