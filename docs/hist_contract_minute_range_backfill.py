"""Backfill of option_quote_minutes' range inside each minute (migration 0052) for the sessions recorded before the stream processor
kept it (Lester 2026-11-02, "si": the Call | Put chart drew SPXW 11-02 P7620's peak at +$4,420 while the contract printed 64.81, +$4,581).

Per 0DTE contract recorded that day (the ones the contract chart can show), ONE ThetaData request: /v3/option/history/trade_quote of that
contract, 09:30-16:00 ET (every trade with the NBBO at that millisecond). Per ET minute:
  trade_high / trade_low = highest / lowest single-leg trade price (multi-leg conditions 130-144 left out, as the live recorder does);
  mid_high / mid_low     = highest / lowest mid of the quotes attached to those trades (a sample of the minute's quotes; the stored last
                           quote of the minute is still part of the range when the chart reads it).
A minute that already has a row gets its range widened (never narrowed: re-running is harmless); a minute that traded but has no row
(its quote did not change) gets one carrying the previous row's bid/ask, exactly what the live recorder now writes.

Rules: off-session only (refuses 09:25-16:10 ET on weekdays), at most 2 requests in flight (the Terminal is shared with the live stream),
never while a stream service restarts. Default is a dry run (counts only); --apply writes.
Usage (server): python docs/hist_contract_minute_range_backfill.py 2026-10-27 2026-10-28 ... [--apply]"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text  # noqa: E402

from backend.core.container import build_container  # noqa: E402
from backend.domain.use_cases.contract_chart import parse_occ  # noqa: E402
from backend.domain.use_cases.minute_flow import MULTI_LEG_CONDITIONS  # noqa: E402

ET = ZoneInfo("America/New_York")
TERMINAL = "http://localhost:25503/v3/option/history/trade_quote"
IN_FLIGHT = 2


def refuse_during_session() -> None:
    now = datetime.now(ET)
    if now.weekday() < 5 and (9, 25) <= (now.hour, now.minute) < (16, 10):
        sys.exit(f"{now:%H:%M} ET: off-session only (09:25-16:10 ET on weekdays is refused)")


def contracts_of(storage, day: date) -> list[str]:
    """The 0DTE contracts recorded that day (their OCC expiration is the day itself)."""
    start, end = datetime.combine(day, datetime.min.time(), ET).replace(hour=9, minute=30), datetime.combine(day, datetime.min.time(), ET).replace(hour=16)
    with storage.session_factory() as session:
        rows = session.execute(
            text("SELECT DISTINCT occ_symbol FROM option_quote_minutes WHERE time >= :start AND time < :end"), {"start": start, "end": end}
        ).all()
    out = []
    for (occ,) in rows:
        parsed = parse_occ(str(occ))
        if parsed is not None and parsed[1] == day:
            out.append(str(occ))
    return sorted(out)


def download(client: httpx.Client, occ: str) -> list[dict]:
    root, expiration, right, strike = parse_occ(occ)  # type: ignore[misc]
    params = dict(
        symbol=root,
        expiration=expiration.isoformat(),
        strike=f"{strike:.2f}",
        right="call" if right == "C" else "put",
        date=expiration.isoformat(),
        start_time="09:30:00.000",
        end_time="16:00:00.000",
        exclusive="true",
        format="ndjson",
        use_time_based_trade_quote="true",
    )
    for attempt in range(3):
        response = client.get(TERMINAL, params=params)
        if response.status_code == 472:  # no data: the contract never traded in the window
            return []
        if response.status_code == 200:
            return [json.loads(line) for line in response.text.splitlines() if line.strip()]
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{occ}: HTTP {response.status_code}")


def minute_ranges(rows: list[dict]) -> dict[datetime, list[float | None]]:
    """ET minute start -> [mid_high, mid_low, trade_high, trade_low] from the trades of one contract."""
    out: dict[datetime, list[float | None]] = {}
    for row in rows:
        stamp = datetime.fromisoformat(str(row["trade_timestamp"])[:16]).replace(tzinfo=ET)
        span = out.setdefault(stamp, [None, None, None, None])
        bid, ask = float(row.get("bid") or 0), float(row.get("ask") or 0)
        if ask > 0 and bid <= ask:
            mid = (max(bid, 0.0) + ask) / 2
            span[0] = mid if span[0] is None else max(span[0], mid)
            span[1] = mid if span[1] is None else min(span[1], mid)
        price, condition = float(row.get("price") or 0), int(row.get("condition") or 0)
        if price > 0 and condition not in MULTI_LEG_CONDITIONS:
            span[2] = price if span[2] is None else max(span[2], price)
            span[3] = price if span[3] is None else min(span[3], price)
    return {minute: span for minute, span in out.items() if any(v is not None for v in span)}


WIDEN = """
UPDATE option_quote_minutes SET
    mid_high = CASE WHEN CAST(:mh AS numeric) IS NULL THEN mid_high ELSE GREATEST(COALESCE(mid_high, :mh), :mh) END,
    mid_low = CASE WHEN CAST(:ml AS numeric) IS NULL THEN mid_low ELSE LEAST(COALESCE(mid_low, :ml), :ml) END,
    trade_high = CASE WHEN CAST(:th AS numeric) IS NULL THEN trade_high ELSE GREATEST(COALESCE(trade_high, :th), :th) END,
    trade_low = CASE WHEN CAST(:tl AS numeric) IS NULL THEN trade_low ELSE LEAST(COALESCE(trade_low, :tl), :tl) END
WHERE time = :time AND occ_symbol = :occ
"""
CARRY = """
INSERT INTO option_quote_minutes (time, occ_symbol, bid, ask, quote_time, mid_high, mid_low, trade_high, trade_low)
VALUES (:time, :occ, :bid, :ask, :quote_time, :mh, :ml, :th, :tl)
ON CONFLICT (time, occ_symbol) DO NOTHING
"""


def price(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(round(value, 4)))


def write(storage, occ: str, day: date, ranges: dict[datetime, list[float | None]]) -> tuple[int, int, int]:
    """(rows widened, rows added carrying the previous quote, minutes skipped: traded before the first recorded quote)."""
    start = datetime.combine(day, datetime.min.time(), ET).replace(hour=9, minute=30)
    widened = added = skipped = 0
    with storage.session_factory.begin() as session:
        existing = session.execute(
            text("SELECT time, bid, ask, quote_time FROM option_quote_minutes WHERE occ_symbol = :occ AND time >= :start AND time < :end ORDER BY time"),
            {"occ": occ, "start": start, "end": start + timedelta(hours=6, minutes=30)},
        ).all()
        by_time = {row.time.astimezone(ET): row for row in existing}
        for minute in sorted(ranges):
            mh, ml, th, tl = (price(v) for v in ranges[minute])
            values = {"time": minute, "occ": occ, "mh": mh, "ml": ml, "th": th, "tl": tl}
            if minute in by_time:
                session.execute(text(WIDEN), values)
                widened += 1
                continue
            before = [row for t, row in by_time.items() if t < minute]
            if not before:
                skipped += 1
                continue
            last = max(before, key=lambda row: row.time)
            session.execute(text(CARRY), {**values, "bid": last.bid, "ask": last.ask, "quote_time": last.quote_time})
            added += 1
    return widened, added, skipped


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("days", nargs="+", type=date.fromisoformat)
    parser.add_argument("--apply", action="store_true", help="write (default: dry run, counts only)")
    args = parser.parse_args()
    refuse_during_session()
    container = build_container()
    storage = container.storage
    client = httpx.Client(timeout=httpx.Timeout(15.0, read=120.0))
    for day in args.days:
        contracts = contracts_of(storage, day)
        print(f"{day}: {len(contracts)} 0DTE contracts recorded", flush=True)
        t0 = time.monotonic()
        totals = dict(requests=0, trades=0, minutes=0, widened=0, added=0, skipped=0, failed=0)
        peak: tuple[float, str, str] | None = None
        with ThreadPoolExecutor(max_workers=IN_FLIGHT) as pool:
            for occ, result in zip(contracts, pool.map(lambda o: _safe_download(client, o), contracts)):
                refuse_during_session()
                totals["requests"] += 1
                if result is None:
                    totals["failed"] += 1
                    continue
                ranges = minute_ranges(result)
                totals["trades"] += len(result)
                totals["minutes"] += len(ranges)
                for minute, span in ranges.items():
                    if span[2] is not None and (peak is None or span[2] > peak[0]) and occ.endswith("P07620000") and occ.startswith("SPXW"):
                        peak = (span[2], occ, f"{minute:%H:%M}")
                if args.apply and ranges:
                    w, a, s = write(storage, occ, day, ranges)
                    totals["widened"] += w
                    totals["added"] += a
                    totals["skipped"] += s
        print(f"{day}: {totals} in {time.monotonic() - t0:.0f} s" + (f"; SPXW P7620 trade high {peak}" if peak else ""), flush=True)


def _safe_download(client: httpx.Client, occ: str) -> list[dict] | None:
    try:
        return download(client, occ)
    except Exception as error:  # one contract failing must not stop the day
        print(f"  {occ}: {type(error).__name__}: {error}", flush=True)
        return None


if __name__ == "__main__":
    main()
