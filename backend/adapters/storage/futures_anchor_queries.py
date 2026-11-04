"""PostgreSQL reads the ES / NQ anchor checks need (backend/domain/use_cases/futures_anchor.py): the index's first-minute median and the
offsets of the last days that have an anchor. Shared by the 09:41 Yahoo task and the NinjaTrader feed endpoint."""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta

from sqlalchemy import text

from backend.domain.use_cases.market_hours import EASTERN_TIME


def first_minute_median(storage, index_symbol: str, day_open: datetime) -> float | None:
    with storage.session_factory() as session:
        rows = session.execute(
            text(
                """SELECT m.price FROM market_snapshots m JOIN underlyings u ON u.id = m.underlying_id
                   WHERE u.symbol = :s AND m.time >= :a AND m.time < :b ORDER BY m.time"""
            ),
            {"s": index_symbol, "a": day_open, "b": day_open + timedelta(minutes=1)},
        ).all()
        session.rollback()
    return statistics.median(float(r[0]) for r in rows) if rows else None


def recent_offsets(storage, future: str, index_symbol: str, before: datetime, sessions: int = 5) -> list[float]:
    """The offsets of the last `sessions` days with an anchor (typed or saved automatically): anchor - median of that day's first minute."""
    with storage.session_factory() as session:
        rows = session.execute(
            text(
                """SELECT a.session_date, a.anchor_price FROM future_price_anchors a JOIN underlyings u ON u.id = a.underlying_id
                   WHERE u.symbol = :f AND a.session_date < :d ORDER BY a.session_date DESC LIMIT :n"""
            ),
            {"f": future, "d": before.date(), "n": sessions},
        ).all()
        session.rollback()
    offsets = []
    for session_date, anchor in rows:
        day_open = datetime(session_date.year, session_date.month, session_date.day, 9, 30, tzinfo=EASTERN_TIME)
        median = first_minute_median(storage, index_symbol, day_open)
        if median is not None:
            offsets.append(float(anchor) - median)
    return offsets
