"""READ-ONLY inventory of the stage-3 trade_quote downloads (days, symbols, datasets, status, rows if present)."""
import json
from collections import Counter, defaultdict
for name in (r"V:\Convexa\hist\raw\stage3_A\manifest_stage3_A.jsonl", r"V:\Convexa\hist\raw\stage3_1016\manifest_stage3_test.jsonl", r"V:\Convexa\hist\raw\stage3_test\manifest_stage3_test.jsonl"):
    rows = []
    for line in open(name, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    print("\n", name.split("\\")[-2], "lines", len(rows), "| keys", sorted(rows[0].keys())[:16])
    print(" status", dict(Counter(str(r.get("status")) for r in rows)))
    print(" datasets", dict(Counter(str(r.get("dataset")) for r in rows)), "| symbols", dict(Counter(str(r.get("symbol")) for r in rows)))
    days = sorted({str(r.get("day")) for r in rows})
    print(" days", len(days), days[:3], "...", days[-3:])
    ok = [r for r in rows if r.get("status") == "downloaded"]
    per_day = Counter(str(r.get("day")) for r in ok)
    print(" downloaded files per day (first 5):", list(per_day.items())[:5])
    if ok:
        print(" sample row:", {k: (v if k != "file" else str(v)[-60:]) for k, v in ok[0].items()})
