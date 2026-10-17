"""READ-ONLY, descriptive, 2026-10-16: the trade-condition mix of each of the 24 SPX alerts between 10:41 and 11:19, rebuilt from the downloaded SPXW trade_quote files (the column whale_alerts.condition_premium only started
filling at 16:56 ET, after these alerts). Rebuild rule, as the engine does it: bucket = one clock minute of the contract's trades (premium = price x size x 100, per condition code); the alert's `time` is the trade that
CLOSED the bucket, so UNUSUAL/WHALE cover the last minute bucket that started before that trade's minute and SUSTAINED_FLOW the last 15 such buckets (only minutes with trades). Prints the rebuilt premium against the stored
`amount` (relative error) so a bad rebuild is visible, and the premium share by condition code. Output rows are JS arrays for the heatmap prototype."""
import glob
import gzip
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text
from backend.core.container import build_container

ET = ZoneInfo("America/New_York")
DAY = "2026-10-16"
RAW = "V:/Convexa/hist/raw/stage3_1016/SPXW/" + DAY
def at(hm): return datetime.strptime(DAY + " " + hm, "%Y-%m-%d %H:%M").replace(tzinfo=ET)
c = build_container()
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY")); s.execute(text("SET LOCAL statement_timeout='60s'"))
    uid = s.execute(text("select id from underlyings where symbol='SPX'")).scalar()
    alerts = s.execute(text("select time, occ_symbol, alert_type, amount from whale_alerts where underlying_id=:u and time>=:a and time<:b order by time"), {"u": uid, "a": at("10:41"), "b": at("11:20")}).all()
    s.rollback()
contracts = set()
for t, occ, typ, amt in alerts:
    m = re.search(r"([CP])(\d{8})$", occ)
    contracts.add((int(m.group(2)) // 1000, m.group(1)))
trades = defaultdict(list)   # (strike, right) -> [(datetime, premium, condition)]
for path in sorted(glob.glob(RAW + "/tq_SPXW_exp%s_*.ndjson.gz" % DAY)):
    m = re.search(r"_(\d{4})-(\d{4})", path)
    if not m or not ("1000" <= m.group(1) and m.group(2) <= "1130"):
        continue
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            try:
                key = (int(float(r["strike"])), r["right"][0])
                if key not in contracts:
                    continue
                dt = datetime.fromisoformat(str(r["trade_timestamp"])).replace(tzinfo=ET)
                trades[key].append((dt, float(r["price"]) * int(r["size"]) * 100, r.get("condition")))
            except (TypeError, ValueError, KeyError):
                continue


def category(code):
    if code == 18: return "simple"
    if code in (125, 126): return "subasta"
    if code is not None and 130 <= code <= 144: return "multi-leg"
    if code in (40, 41, 42, 43, 44, 148): return "cancelacion"
    return "otra"


print("time | contract | type | stored amount | rebuilt | error | share by category (premium) | codes")
rows = []
for t, occ, typ, amt in alerts:
    m = re.search(r"([CP])(\d{8})$", occ)
    key = (int(m.group(2)) // 1000, m.group(1))
    minute_of = t.replace(second=0, microsecond=0)
    buckets = defaultdict(lambda: defaultdict(float))
    for dt, prem, cond in trades[key]:
        mb = dt.replace(second=0, microsecond=0)
        if mb < minute_of:
            buckets[mb][cond] += prem
    starts = sorted(buckets)
    use = starts[-1:] if typ in ("UNUSUAL", "WHALE") else starts[-15:]
    by_code = defaultdict(float)
    for mb in use:
        for cond, prem in buckets[mb].items():
            by_code[cond] += prem
    tot = sum(by_code.values())
    err = abs(tot - float(amt)) / float(amt) if float(amt) else 0
    cat = defaultdict(float)
    for cond, prem in by_code.items():
        cat[category(cond)] += prem
    shares = {k: round(100 * v / tot) for k, v in sorted(cat.items(), key=lambda kv: -kv[1])} if tot else {}
    print("%s | %s%s | %s | $%.0fk | $%.0fk | %.1f%% | %s | %s" % (t.astimezone(ET).strftime("%H:%M:%S"), key[1], key[0], typ, float(amt) / 1000, tot / 1000, 100 * err, shares, {k: round(v / 1000) for k, v in by_code.items()}))
    rows.append((t.astimezone(ET).strftime("%H:%M:%S"), shares, err))
print("\nJS rows (time, shares by category, rebuild error):")
for tm, sh, err in rows:
    print("['%s', %s, %.3f]," % (tm, json.dumps(sh), err))
