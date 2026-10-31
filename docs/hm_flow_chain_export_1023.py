"""READ-ONLY. Data for the flow chain design prototype (2026-10-25): SPX, Friday 2026-10-23, per ET minute 09:30-15:59.

- spot (market_snapshots), Convexa's own levels per minute (gamma_aggregates, tactical view: walls, flip, ABS gamma),
  net GEX per strike (gamma_aggregate_items, tactical, last cycle of the minute);
- REAL per-strike flow from the downloaded trades (V:/Convexa/hist/raw/stage3_1023/SPXW/2026-10-23, 0DTE only), same rule as the minute Flujo
  panel: only simple trades (condition 18) signed against the trade's own bid/ask (at/above ask buy, at/below bid sell, above/below mid, mid = not
  signed); multi-leg legs (130-144) and every other condition kept APART (never mixed into a side); size = contracts x |delta| (delta of the
  contract's latest stored chain snapshot at or before that minute), sign: buy call +, sell call -, buy put -, sell put + (positive = bullish);
- storage estimate for a per-strike minute table: cells (minute, strike, right) with at least one trade.
Writes V:/Convexa/repo/logs/flow_chain_export_1023.json. Counts only in the console.
"""
import bisect
import glob
import gzip
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text

from backend.core.container import build_container

ET = ZoneInfo("America/New_York")
A, B = datetime(2026, 10, 23, 9, 30, tzinfo=ET), datetime(2026, 10, 23, 16, 0, tzinfo=ET)
TRADES = "V:/Convexa/hist/raw/stage3_1023/SPXW/2026-10-23"
OUT = "V:/Convexa/repo/logs/flow_chain_export_1023.json"
ML = set(range(130, 145))


def minute_index(t):
    return (t.hour - 9) * 60 + t.minute - 30


c = build_container()
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    uid = s.execute(text("select id from underlyings where symbol='SPX'")).scalar()
    spot = [None] * 390
    for t, p in s.execute(text("select time, price from market_snapshots where underlying_id=:u and time>=:a and time<:b order by time"), {"u": uid, "a": A, "b": B}):
        spot[minute_index(t.astimezone(ET))] = float(p)
    levels = [None] * 390
    for r in s.execute(text("select * from gamma_aggregates where underlying_id=:u and view='tactical' and time>=:a and time<:b order by time"), {"u": uid, "a": A, "b": B}).mappings():
        keep = {k: (float(v) if v is not None else None) for k, v in r.items() if any(w in k for w in ("wall", "flip", "absolute_gamma_strike", "abs_gamma"))}
        levels[minute_index(r["time"].astimezone(ET))] = keep
    gex = [None] * 390
    rows = s.execute(text("""select time, strike, net_gamma from gamma_aggregate_items where underlying_id=:u and view='tactical' and time>=:a and time<:b
                             and strike between 7450 and 7850 order by time"""), {"u": uid, "a": A, "b": B})
    last_t = {}
    for t, k, net in rows:
        m = minute_index(t.astimezone(ET))
        if last_t.get(m) != t:
            last_t[m], gex[m] = t, {}
        gex[m][float(k)] = float(net)
    # delta per contract over the day (cycle snapshots), for the trade sizing
    deltas = defaultdict(list)  # (right, strike) -> [(epoch, |delta|)]
    q = text("""select snap.time, oc.contract_type, oc.strike, snap.delta from option_chain_snapshots snap join option_contracts oc on oc.id = snap.contract_id
                where oc.underlying_id=:u and oc.expiration = '2026-10-23' and snap.time>=:a and snap.time<:b and snap.delta is not null""")
    for t, ctype, k, d in s.execute(q, {"u": uid, "a": A, "b": B}):
        deltas[("C" if str(ctype).upper().startswith("C") else "P", float(k))].append((t.timestamp(), abs(float(d))))
    for v in deltas.values():
        v.sort()
    s.rollback()


def delta_at(right, strike, epoch):
    series = deltas.get((right, strike))
    if not series:
        return None
    i = bisect.bisect_right([e for e, _ in series], epoch) - 1
    return series[max(i, 0)][1]


flow = [defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0, 0, 0]) for _ in range(390)]  # strike -> [calls_signed, puts_signed, buy_prem, sell_prem, ml_contracts, other_contracts, signed_contracts]
cells, n_trades, no_delta = set(), 0, 0
for path in sorted(glob.glob(f"{TRADES}/*.ndjson.gz")):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                ts = r["trade_timestamp"]
                if ts[:10] != "2026-10-23" or not ("09:30:00" <= ts[11:19] < "16:00:00"):
                    continue
                right, k, p, size, cond = str(r["right"])[0].upper(), float(r["strike"]), float(r["price"]), int(r["size"]), int(r["condition"])
                bid, ask = float(r["bid"]), float(r["ask"])
            except (TypeError, ValueError, KeyError):
                continue
            n_trades += 1
            t = datetime(2026, 10, 23, int(ts[11:13]), int(ts[14:16]), int(ts[17:19]), tzinfo=ET)
            m = minute_index(t)
            cells.add((m, k, right))
            cell = flow[m][k]
            if cond in ML:
                cell[4] += size
                continue
            if cond != 18 or bid <= 0 or ask <= 0:
                cell[5] += size
                continue
            mid = (bid + ask) / 2
            side = 1 if p >= ask or p > mid else (-1 if p <= bid or p < mid else 0)
            if side == 0:
                cell[5] += size
                continue
            d = delta_at(right, k, t.timestamp())
            if d is None:
                no_delta += 1
                cell[5] += size
                continue
            deq = size * d
            if right == "C":
                cell[0] += side * deq
            else:
                cell[1] += -side * deq
            cell[2 if side > 0 else 3] += p * size * 100
            cell[6] += size

out = {"symbol": "SPX", "day": "2026-10-23", "spot": spot, "levels": levels,
       "gex": [None if g is None else {str(k): v for k, v in g.items()} for g in gex],
       "flow": [{str(k): [round(x, 2) if isinstance(x, float) else x for x in v] for k, v in f.items()} for f in flow]}
with open(OUT, "w", encoding="utf-8") as fh:
    json.dump(out, fh)
print("minutes with spot", sum(1 for x in spot if x), "| with levels", sum(1 for x in levels if x), "| with gex", sum(1 for x in gex if x))
print("level keys", sorted(next(x for x in levels if x).keys()) if any(levels) else None)
print("trades", n_trades, "| no delta", no_delta, "| cells (minute, strike, right) with trades", len(cells))
tot = [0, 0, 0]
for f in flow:
    for v in f.values():
        tot[0] += v[6]; tot[1] += v[4]; tot[2] += v[5]
print("contracts signed", tot[0], "| multi-leg", tot[1], "| other / unsigned", tot[2])
print("written", OUT, round(os.path.getsize(OUT) / 1e6, 2), "MB")
