"""Backfill of minute_strike_flow (migration 0050) from the downloaded ThetaData trade_quote files (V:/Convexa/hist/raw/stage3_*/<ROOT>/<day>/*.ndjson.gz).

Same rule as the live stream processor (backend/domain/use_cases/minute_flow.py): a simple trade (condition 18) at or above ITS OWN ask is a buy, at or below its bid a sell, anything
else unsigned; multi-leg legs (130-144) counted apart as packages; size = contracts x |delta|, the delta of the contract's stored chain snapshot of that minute (the latest one at or before
it); sign buy call +, sell call -, buy put -, sell put +. Only the regular session 09:30-16:00 ET. Difference with live: the bid/ask is the one carried by each downloaded trade (the
live processor uses the last streamed quote), and contracts without a stored snapshot (expirations the scheduler did not cover) are counted as unsigned.

DRY RUN by default (counts only). --apply writes, ON CONFLICT DO NOTHING, so a re-run never doubles a cell; never run it before migration 0050 is applied, never during market hours.
Usage: python backfill_minute_strike_flow.py --folder stage3_NDXP --roots NDXP,IWM [--days 2026-09-08,...] [--apply]
"""
import argparse
import bisect
import glob
import gzip
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text

from backend.core.container import build_container

ET = ZoneInfo("America/New_York")
RAW = "V:/Convexa/hist/raw"
SYMBOL_BY_ROOT = {"SPXW": "SPX", "SPX": "SPX", "NDXP": "NDX", "NDX": "NDX", "SPY": "SPY", "QQQ": "QQQ", "IWM": "IWM", "NVDA": "NVDA"}
MULTI_LEG = set(range(130, 145))
BATCH = 5_000

parser = argparse.ArgumentParser()
parser.add_argument("--folder", required=True)
parser.add_argument("--roots", required=True)
parser.add_argument("--days", default="")
parser.add_argument("--apply", action="store_true")
args = parser.parse_args()

if args.apply:
    now = datetime.now(ET)
    if now.weekday() < 5 and 9 <= now.hour < 17:
        sys.exit("refused: market hours (run after 17:00 ET or on a weekend)")

c = build_container()
INSERT = text(
    """
    INSERT INTO minute_strike_flow (minute, symbol, expiration, strike, option_right, delta_flow, contracts_signed, contracts_package, contracts_other, buy_premium, sell_premium)
    VALUES (:minute, :symbol, :expiration, :strike, :right, :flow, :signed, :package, :other, :buy, :sell)
    ON CONFLICT (symbol, minute, expiration, strike, option_right) DO NOTHING
    """
)


def load_deltas(symbol: str, day: date) -> dict:
    """(expiration, right, strike) -> ([minute epochs], [|delta|]) from the stored chain snapshots of that session, one value per minute."""
    a = datetime(day.year, day.month, day.day, 9, 25, tzinfo=ET)
    b = datetime(day.year, day.month, day.day, 16, 5, tzinfo=ET)
    out = defaultdict(lambda: ([], []))
    with c.storage.session_factory() as s:
        s.execute(text("SET TRANSACTION READ ONLY"))
        s.execute(text("SET LOCAL statement_timeout = '300s'"))
        rows = s.execute(
            text(
                """
                SELECT date_trunc('minute', snap.time) AS m, oc.expiration, oc.contract_type, oc.strike, avg(abs(snap.delta))
                FROM option_chain_snapshots snap JOIN option_contracts oc ON oc.id = snap.contract_id JOIN underlyings u ON u.id = oc.underlying_id
                WHERE u.symbol = :symbol AND snap.time >= :a AND snap.time < :b AND snap.delta IS NOT NULL
                GROUP BY 1, 2, 3, 4 ORDER BY 1
                """
            ),
            {"symbol": symbol, "a": a, "b": b},
        )
        for m, exp, ctype, strike, d in rows:
            series = out[(exp, "C" if str(ctype).upper().startswith("C") else "P", float(strike))]
            series[0].append(m.timestamp())
            series[1].append(float(d))
        s.rollback()
    return out


def delta_at(deltas: dict, key, epoch: float):
    series = deltas.get(key)
    if not series:
        return None
    i = bisect.bisect_right(series[0], epoch) - 1
    return series[1][max(i, 0)]


def one_day(root: str, day_dir: str) -> tuple[list[dict], dict]:
    symbol = SYMBOL_BY_ROOT[root]
    day = date.fromisoformat(os.path.basename(day_dir))
    deltas = load_deltas(symbol, day)
    cells = defaultdict(lambda: [0.0, 0, 0, 0, 0.0, 0.0])  # (minute epoch, expiration, strike, right) -> flow, signed, package, other, buy, sell
    stats = defaultdict(int)
    for path in sorted(glob.glob(os.path.join(day_dir, "*.ndjson.gz"))):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    ts = r["trade_timestamp"]
                    if ts[:10] != day.isoformat() or not ("09:30:00" <= ts[11:19] < "16:00:00"):
                        continue
                    right, strike, price, size, cond = str(r["right"])[0].upper(), float(r["strike"]), float(r["price"]), int(r["size"]), int(r["condition"])
                    bid, ask, exp = float(r["bid"]), float(r["ask"]), date.fromisoformat(str(r["expiration"])[:10])
                except (TypeError, ValueError, KeyError):
                    stats["bad_rows"] += 1
                    continue
                if strike > 100_000:  # some feeds give the strike in thousandths
                    strike /= 1000
                minute = datetime(day.year, day.month, day.day, int(ts[11:13]), int(ts[14:16]), tzinfo=ET).timestamp()
                cell = cells[(minute, exp, strike, right)]
                stats["trades"] += 1
                if cond in MULTI_LEG:
                    cell[2] += size
                    continue
                if cond != 18 or size <= 0 or bid <= 0 or ask <= 0:
                    cell[3] += size
                    continue
                sign = 1 if price >= ask else (-1 if price <= bid else 0)
                d = delta_at(deltas, (exp, right, strike), minute) if sign else None
                if not sign or d is None:
                    stats["no_delta" if sign else "inside"] += 1
                    cell[3] += size
                    continue
                flow = sign * size * d
                cell[0] += flow if right == "C" else -flow
                cell[1] += size
                cell[4 if sign > 0 else 5] += price * size * 100
    rows = [
        {
            "minute": datetime.fromtimestamp(m, tz=ET), "symbol": symbol, "expiration": exp, "strike": strike, "right": right,
            "flow": round(v[0], 4), "signed": v[1], "package": v[2], "other": v[3], "buy": round(v[4], 2), "sell": round(v[5], 2),
        }
        for (m, exp, strike, right), v in cells.items()
    ]
    stats["cells"] = len(rows)
    stats["contracts_signed"] = sum(r["signed"] for r in rows)
    stats["contracts_package"] = sum(r["package"] for r in rows)
    stats["contracts_other"] = sum(r["other"] for r in rows)
    return rows, stats


wanted = {d for d in args.days.split(",") if d}
for root in [r.strip().upper() for r in args.roots.split(",") if r.strip()]:
    for day_dir in sorted(glob.glob(f"{RAW}/{args.folder}/{root}/2026-*")):
        if wanted and os.path.basename(day_dir) not in wanted:
            continue
        started = time.monotonic()
        rows, stats = one_day(root, day_dir)
        if args.apply and rows:
            for i in range(0, len(rows), BATCH):
                with c.storage.session_factory() as s:
                    s.execute(INSERT, rows[i : i + BATCH])
                    s.commit()
        total = stats["contracts_signed"] + stats["contracts_package"] + stats["contracts_other"]
        print(
            f"{root} {os.path.basename(day_dir)}: trades {stats['trades']} cells {stats['cells']} | signed {stats['contracts_signed']} "
            f"({stats['contracts_signed'] / total:.0%}) package {stats['contracts_package']} other {stats['contracts_other']} | no delta {stats['no_delta']} "
            f"| {'WRITTEN' if args.apply else 'dry run'} in {time.monotonic() - started:.0f} s"
            if total else f"{root} {os.path.basename(day_dir)}: no trades"
        )
