"""READ-ONLY, descriptive, 2026-10-16: how the code-129 (single-leg floor trade) prints of the P7725 alert (10:50) sat against the bid/ask stored with each trade in the SPXW trade_quote files,
and the overall share of code-129 premium in the whole file set (does 129 matter beyond this one alert?). Counts and shares only."""
import glob
import gzip
import json
import re
from collections import defaultdict

RAW = "V:/Convexa/hist/raw/stage3_1016/SPXW/2026-10-16"
by_code = defaultdict(float)
rows = []
for path in sorted(glob.glob(RAW + "/tq_SPXW_exp2026-10-16_*.ndjson.gz")):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            try:
                prem = float(r["price"]) * int(r["size"]) * 100
                by_code[r.get("condition")] += prem
                if int(float(r["strike"])) == 7725 and r["right"][0] == "P" and r.get("condition") == 129 and "10:49" <= str(r["trade_timestamp"])[11:16] <= "10:50":
                    rows.append(r)
            except (TypeError, ValueError, KeyError):
                continue
tot = sum(by_code.values())
print("premium share by code, whole SPXW 0DTE file set (codes with >= 0.5%%):")
for k, v in sorted(by_code.items(), key=lambda kv: -kv[1]):
    if v / tot >= 0.005:
        print("  code %s: %.1f%%  ($%.1fM)" % (k, 100 * v / tot, v / 1e6))
print("\nP7725 code-129 prints at 10:49-10:50: %d" % len(rows))
print("keys in a row:", sorted(rows[0].keys()) if rows else None)
for r in rows[:12]:
    print({k: r.get(k) for k in ("trade_timestamp", "price", "size", "bid", "ask", "bid_size", "ask_size", "exchange") if k in r})
