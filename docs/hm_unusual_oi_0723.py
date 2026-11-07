"""READ-ONLY: check the OI column of the mentor's 'Actividad Inusual' table (2026-07-23, 0DTE) against the stage-2 OI file."""
import glob, gzip, json
D = r"V:\Convexa\hist\raw\stage2"
DAY = "2026-07-23"
rows = [("PUT", 7400, 2474), ("PUT", 7420, 1832), ("PUT", 7410, 1377), ("PUT", 7425, 1711), ("PUT", 7430, 2870), ("PUT", 7415, 1295), ("CALL", 7400, 89), ("PUT", 7450, 2207),
        ("PUT", 7440, 1777), ("PUT", 7405, 1254), ("PUT", 7435, 1081), ("CALL", 7410, 39), ("PUT", 7395, 1526), ("PUT", 7500, 3119), ("CALL", 7390, 113), ("PUT", 7445, 1198),
        ("CALL", 7405, 13), ("CALL", 7415, 16)]
oi_f = None
for p in glob.glob(D + r"\manifest_*tanda*.jsonl"):
    for line in open(p, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            if r.get("status") == "downloaded" and r.get("symbol") == "SPXW" and r.get("day") == DAY and r["dataset"] == "oi":
                oi_f = r["file"]
body = json.loads(gzip.open(oi_f, "rt", encoding="utf-8").read())
body = body["response"] if isinstance(body, dict) and "response" in body else body
oi = {}
for e in body:
    c = e["contract"]
    if c["expiration"] == DAY:
        oi[(c["right"], int(float(c["strike"])))] = e["data"][0]["open_interest"] if e["data"] else 0
ok = 0
for right, k, theirs in rows:
    ours = oi.get((right, k))
    ok += (ours == theirs)
    print("%-4s %d  mentor OI %5d | stage-2 OI %s %s" % (right, k, theirs, ours, "OK" if ours == theirs else "DIFF"))
print("exact matches: %d of %d" % (ok, len(rows)))
