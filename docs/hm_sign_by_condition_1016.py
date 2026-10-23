"""READ-ONLY, descriptive, 2026-10-16: what would option (a) of design 12.12 change? Option (a) = sign a trade buy/sell only when its condition is 18 (simple), 95 (sweep), 125 or 126 (single-leg auction);
every other code (multi-leg 130-144, floor 129, crosses 127/128, ...) stays "no side". Uses the engine's own rule (calculate_lee_ready.classify_trade_side, quote rule with the bid/ask stored in each
trade_quote row, tick rule at the midpoint; previous price/side chained over ALL trades of the contract as the engine does) on the SPXW 0DTE files of 2026-10-16.
Part 1: the 24 alerts of 10:41-11:19 (same bucket rebuild as hm_alert_conditions_1016.py): label today vs label under (a). Part 2: the whole day: premium that becomes "no side", and the net signed premium (buy - sell) for calls/puts."""
import glob
import gzip
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text
from backend.core.container import build_container
from backend.domain.entities import Side
from backend.domain.use_cases.calculate_lee_ready import classify_trade_side

ET = ZoneInfo("America/New_York")
DAY = "2026-10-16"
RAW = "V:/Convexa/hist/raw/stage3_1016/SPXW/" + DAY
SIGNED_CODES = {18, 95, 125, 126}
def at(hm): return datetime.strptime(DAY + " " + hm, "%Y-%m-%d %H:%M").replace(tzinfo=ET)


c = build_container()
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY")); s.execute(text("SET LOCAL statement_timeout='60s'"))
    uid = s.execute(text("select id from underlyings where symbol='SPX'")).scalar()
    alerts = s.execute(text("select time, occ_symbol, alert_type, amount, estimated_buy_volume, estimated_sell_volume from whale_alerts where underlying_id=:u and time>=:a and time<:b order by time"), {"u": uid, "a": at("10:41"), "b": at("11:20")}).all()
    s.rollback()
want = set()
for t, occ, typ, amt, b, sl in alerts:
    m = re.search(r"([CP])(\d{8})$", occ)
    want.add((int(m.group(2)) // 1000, m.group(1)))

prev = {}                                   # contract -> (price, side)
alert_trades = defaultdict(list)            # contract -> [(dt, premium, code, side)]
day_prem = defaultdict(float)               # (right, 'all'|'a') -> signed (buy - sell) premium
day_total = 0.0
day_nosign_a = 0.0
for path in sorted(glob.glob(RAW + "/tq_SPXW_exp%s_*.ndjson.gz" % DAY)):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        rows = []
        for line in fh:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    rows.sort(key=lambda r: (str(r.get("trade_timestamp")), r.get("sequence") or 0))
    for r in rows:
        try:
            key = (int(float(r["strike"])), r["right"][0])
            price = Decimal(str(r["price"]))
            size = int(r["size"])
            bid = Decimal(str(r["bid"])) if r.get("bid") not in (None, 0) else None
            ask = Decimal(str(r["ask"])) if r.get("ask") not in (None, 0) else None
            code = r.get("condition")
            dt = datetime.fromisoformat(str(r["trade_timestamp"])).replace(tzinfo=ET)
        except (TypeError, ValueError, KeyError, ArithmeticError):
            continue
        pp, ps = prev.get(key, (None, Side.UNKNOWN))
        side = classify_trade_side(price, bid, ask, pp, ps)
        prev[key] = (price, side)
        prem = float(price) * size * 100
        sgn = 1 if side is Side.BUY else -1 if side is Side.SELL else 0
        day_total += prem
        day_prem[(key[1], "all")] += sgn * prem
        if code in SIGNED_CODES:
            day_prem[(key[1], "a")] += sgn * prem
        else:
            day_nosign_a += prem
        if key in want:
            alert_trades[key].append((dt, prem, code, side))


def label(buy, sell, total):
    if total <= 0 or (buy + sell) < 0.5 * total:
        return "SIN LADO"
    share = buy / (buy + sell) if (buy + sell) else 0.5
    return "COMPRA" if share >= 0.6 else "VENTA" if share <= 0.4 else "SIN LADO CLARO"


print("PART 1 - the 24 alerts: time | contract | type | stored buy%% | label today (rebuilt, signs all) | label under (a) | premium that becomes no-side under (a)")
changed = 0
for t, occ, typ, amt, sb, ss in alerts:
    m = re.search(r"([CP])(\d{8})$", occ)
    key = (int(m.group(2)) // 1000, m.group(1))
    minute_of = t.replace(second=0, microsecond=0)
    buckets = defaultdict(list)
    for dt, prem, code, side in alert_trades[key]:
        mb = dt.replace(second=0, microsecond=0)
        if mb < minute_of:
            buckets[mb].append((prem, code, side))
    starts = sorted(buckets)
    use = starts[-1:] if typ in ("UNUSUAL", "WHALE") else starts[-15:]
    allb = alls = ab = as_ = tot = nos = 0.0
    for mb in use:
        for prem, code, side in buckets[mb]:
            tot += prem
            if side is Side.BUY: allb += prem
            elif side is Side.SELL: alls += prem
            else: allb += prem / 2; alls += prem / 2
            if code in SIGNED_CODES:
                if side is Side.BUY: ab += prem
                elif side is Side.SELL: as_ += prem
                else: ab += prem / 2; as_ += prem / 2
            else:
                nos += prem
    l_all, l_a = label(allb, alls, tot), label(ab, as_, tot)
    changed += 1 if l_all != l_a else 0
    sbp = 100 * float(sb) / (float(sb) + float(ss)) if float(sb) + float(ss) else 50
    print("%s | %s%d | %s | %3.0f%% | %s | %s | %.0f%% %s" % (t.astimezone(ET).strftime("%H:%M:%S"), key[1], key[0], typ, sbp, l_all, l_a, 100 * nos / tot if tot else 0, "<-- CHANGES" if l_all != l_a else ""))
print("labels that change under (a): %d of %d" % (changed, len(alerts)))

print("\nPART 2 - whole day SPXW 0DTE: total premium $%.1fM; premium becoming no-side under (a): %.1f%%" % (day_total / 1e6, 100 * day_nosign_a / day_total))
for right in ("C", "P"):
    print("net signed premium (buy - sell) %s: today $%.1fM | under (a) $%.1fM" % (right, day_prem[(right, "all")] / 1e6, day_prem[(right, "a")] / 1e6))
