"""READ-ONLY: the replay branch's day read (backend/domain/use_cases/replay.py, copied next to this file as replay_branch.py) run against the server's
stored data before it is deployed: time and payload size per symbol and day, plus one day's payload, days list and heatmap dumped to JSON for a local
look at the window. Nothing is written to the database; the two new storage reads are the branch's own SQL run on a read-only session."""
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

sys.path.insert(0, "V:/Convexa/repo")
sys.path.insert(0, "V:/Convexa/repo/logs")
from sqlalchemy import text

import backend.domain.entities as entities


@dataclass(frozen=True, slots=True)
class LevelCycle:
    time: datetime
    call_wall: Decimal | None
    put_wall: Decimal | None
    absolute_gamma_strike: Decimal | None
    gamma_flip: Decimal | None


entities.LevelCycle = LevelCycle  # main has no LevelCycle yet; the branch module imports it from here
import replay_branch as replay  # noqa: E402

from backend.core.container import build_container  # noqa: E402

OUT = "V:/Convexa/bench/replay_check"
c = build_container()


def level(value):
    return None if value is None or Decimal(str(value)) == 0 else Decimal(str(value))


class ReadOnlyBranchStorage:
    """The branch's async reads over the server's sync storage, every query in a READ ONLY transaction."""

    async def get_price_history(self, underlying, start, end):
        return c.storage.get_price_history(underlying, start, end)

    async def get_future_price_anchor(self, symbol, session_date):
        return c.storage.get_future_price_anchor(symbol, session_date)

    async def get_session_dates(self, underlying, first_day, last_day):
        with c.storage.session_factory() as s:
            s.execute(text("SET TRANSACTION READ ONLY"))
            rows = s.execute(text("""
                SELECT d::date AS day
                FROM generate_series(CAST(:first AS date), CAST(:last AS date), interval '1 day') AS d
                WHERE EXISTS (
                    SELECT 1 FROM market_snapshots AS m
                    WHERE m.underlying_id = (SELECT id FROM underlyings WHERE symbol = :symbol)
                      AND m.time >= (d::date + TIME '09:30') AT TIME ZONE 'America/New_York'
                      AND m.time < (d::date + TIME '16:00') AT TIME ZONE 'America/New_York'
                )
                ORDER BY day DESC"""), {"symbol": underlying.upper(), "first": first_day, "last": last_day}).all()
            s.rollback()
        return [row.day for row in rows]

    async def get_level_history(self, underlying, start, end, view):
        with c.storage.session_factory() as s:
            s.execute(text("SET TRANSACTION READ ONLY"))
            rows = s.execute(text("""
                SELECT g.time, g.call_wall, g.put_wall, g.absolute_gamma_strike, g.gamma_flip
                FROM gamma_aggregates AS g
                WHERE g.underlying_id = (SELECT id FROM underlyings WHERE symbol = :symbol)
                  AND g.view = :view AND g.time >= :start AND g.time < :end
                ORDER BY g.time"""), {"symbol": underlying.upper(), "view": view, "start": start, "end": end}).all()
            s.rollback()
        return [LevelCycle(r.time, level(r.call_wall), level(r.put_wall), level(r.absolute_gamma_strike), level(r.gamma_flip)) for r in rows]


def iso(moment):
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def num(value):
    return None if value is None else float(value)


def payload(r):
    candles = lambda cs: [{"time": iso(x.time), "open": float(x.open), "high": float(x.high), "low": float(x.low), "close": float(x.close)} for x in cs]  # noqa: E731
    return {
        "schema_version": 1, "symbol": r.symbol, "source_symbol": r.source_symbol, "session_date": r.session_date.isoformat(), "view": r.view,
        "live": r.live, "session_open": iso(r.session_open), "session_close": iso(r.session_close), "price_offset": float(r.price_offset),
        "anchored": r.anchored, "candles_1m": candles(r.candles_1m), "candles_5m": candles(r.candles_5m),
        "levels": [{"time": iso(x.time), "call_wall": num(x.call_wall), "put_wall": num(x.put_wall), "absolute_gamma_strike": num(x.absolute_gamma_strike), "gamma_flip": num(x.gamma_flip)} for x in r.levels],
        "vwap": [{"time": iso(t), "value": float(v)} for t, v in r.vwap], "vwap_not_applicable": r.vwap_not_applicable, "vwap_proxy_symbol": r.vwap_proxy_symbol,
    }


async def main():
    import os

    from backend.api.serializers import gamma_heatmap_response

    os.makedirs(OUT, exist_ok=True)
    storage = ReadOnlyBranchStorage()
    t0 = time.perf_counter()
    days = await replay.get_replay_days(storage, "SPX")
    print(f"days SPX: today {days.today} has_data {days.today_has_data}, sessions {[d.isoformat() for d in days.sessions]} ({(time.perf_counter() - t0) * 1000:.0f} ms)")
    ndays = await replay.get_replay_days(storage, "NQ")
    print(f"days NQ (NDX): sessions {[d.isoformat() for d in ndays.sessions]}")
    for symbol in ("SPX", "ES", "NDX", "NQ", "QQQ", "IWM", "NVDA", "VIX"):
        for day in (days.today, *days.sessions[:2]):
            replay.clear_replay_cache()
            t0 = time.perf_counter()
            r = await replay.get_replay_day(storage, symbol, day)
            ms = (time.perf_counter() - t0) * 1000
            body = json.dumps(payload(r))
            lv = [x for x in r.levels if x.call_wall is not None or x.put_wall is not None]
            print(f"{symbol} {day}: {ms:.0f} ms, {len(body) / 1024:.0f} KB, 1m {len(r.candles_1m)}, 5m {len(r.candles_5m)}, cycles {len(r.levels)} (with walls {len(lv)}), "
                  f"vwap {len(r.vwap)}{' n/a' if r.vwap_not_applicable else ''}, offset {r.price_offset:+.2f} anchored {r.anchored}, live {r.live}"
                  + (f", first cycle {r.levels[0].time.astimezone(replay.EASTERN_TIME):%H:%M}" if r.levels else ""))
    # Anchors stored for the earlier sessions (design section 7: not checked then).
    for fut in ("ES", "NQ"):
        have = [d.isoformat() for d in days.sessions if c.storage.get_future_price_anchor(fut, d) is not None]
        print(f"{fut} anchors on the last sessions: {have}")
    # One full day for the local look: SPX and ES of the latest finished session, with the heatmap the page asks for.
    day = days.sessions[0]
    with open(f"{OUT}/days_SPX.json", "w") as f:
        json.dump({"schema_version": 1, "symbol": "SPX", "today": days.today.isoformat(), "today_has_data": days.today_has_data, "sessions": [d.isoformat() for d in days.sessions]}, f)
    for symbol in ("SPX", "ES", "NDX"):
        replay.clear_replay_cache()
        r = await replay.get_replay_day(storage, symbol, day)
        with open(f"{OUT}/day_{symbol}_{day}.json", "w") as f:
            json.dump(payload(r), f)
    for source in ("SPX", "NDX"):
        start, end = replay.session_window(day)
        t0 = time.perf_counter()
        heatmap = await c.async_market_storage.get_gamma_heatmap(source, "tactical", "gamma", start, end, 120, "cycle")
        body = gamma_heatmap_response(heatmap)
        text_body = json.dumps(body, default=str)
        print(f"heatmap {source} {day} 2-min: {(time.perf_counter() - t0) * 1000:.0f} ms, {len(text_body) / 1024:.0f} KB, {len(heatmap.strikes)} strikes x {len(heatmap.columns)} columns")
        with open(f"{OUT}/heatmap_{source}_{day}.json", "w") as f:
            f.write(text_body)


asyncio.run(main())
