"""READ-ONLY: size of the stage-3 trade_quote data per session (rows from the manifests) and which sessions also have stage-2 5-minute greeks (for the underlying price path)."""
import glob, json
from collections import defaultdict
rows = defaultdict(lambda: defaultdict(int))
for m in (r"V:\Convexa\hist\raw\stage3_A\manifest_stage3_A.jsonl", r"V:\Convexa\hist\raw\stage3_test\manifest_stage3_test.jsonl", r"V:\Convexa\hist\raw\stage3_1016\manifest_stage3_test.jsonl"):
    for line in open(m, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("status") == "downloaded" and r.get("kind") == "session" and r["params"].get("expiration") == r["params"].get("date"):
            rows[(r["symbol"], r["day"])]["rows"] += (r.get("check") or {}).get("rows", 0)
            rows[(r["symbol"], r["day"])]["files"] += 1
s2 = set()
for p in glob.glob(r"V:\Convexa\hist\raw\stage2\manifest_*tanda*.jsonl"):
    for line in open(p, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            if r.get("status") == "downloaded" and r["dataset"] == "greeks5m" and r["params"]["expiration"] == r["day"]:
                s2.add((r["symbol"], r["day"]))
for sym, root in (("SPXW", "SPXW"), ("SPY", "SPY")):
    days = sorted(d for (s, d) in rows if s == sym)
    tot = sum(rows[(sym, d)]["rows"] for d in days)
    print(sym, "sessions with 0DTE session trade_quote:", len(days), "| total rows %.1fM" % (tot / 1e6), "| with stage-2 5-min greeks (0DTE file):", sum((root, d) in s2 for d in days))
    print("  days:", days[0], "...", days[-1], "| rows per day (min / median / max): %d / %d / %d" % (min(rows[(sym, d)]["rows"] for d in days), sorted(rows[(sym, d)]["rows"] for d in days)[len(days) // 2], max(rows[(sym, d)]["rows"] for d in days)))
    print("  without stage-2 price path:", [d for d in days if (root, d) not in s2])
