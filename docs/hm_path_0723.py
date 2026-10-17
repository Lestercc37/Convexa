"""READ-ONLY: SPX 5-minute path on 2026-07-23 from the stage-2 SPXW file: first bars, after-13:45 extremes, last bars."""
import glob, gzip, json, statistics
D = r"V:\Convexa\hist\raw\stage2"
f = None
for p in glob.glob(D + r"\manifest_*tanda*.jsonl"):
    for line in open(p, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            if r.get("status") == "downloaded" and r.get("symbol") == "SPXW" and r.get("day") == "2026-07-23" and r["dataset"] == "greeks5m" and r["params"]["expiration"] == "2026-07-23":
                f = r["file"]
body = json.loads(gzip.open(f, "rt", encoding="utf-8").read())
body = body["response"] if isinstance(body, dict) and "response" in body else body
path = {}
for e in body[:120]:
    for b in e["data"]:
        if b.get("underlying_price", 0) > 0:
            path.setdefault(b["timestamp"][11:16], []).append(b["underlying_price"])
pts = {k: statistics.median(v) for k, v in path.items() if len(v) >= 5}
ks = sorted(pts)
print("bars", len(ks), "first", ks[:3], "last", ks[-3:])
print("09:30..09:45:", {k: round(pts[k], 2) for k in ks if k <= "09:45"})
after = {k: v for k, v in pts.items() if k >= "13:45"}
print("from 13:45: low %.2f at %s | high %.2f at %s" % (min(after.values()), min(after, key=after.get), max(after.values()), max(after, key=after.get)))
print("15:45..16:00:", {k: round(pts[k], 2) for k in ks if k >= "15:45"})
