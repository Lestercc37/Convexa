"""The replay's two Postgres reads (AsyncPostgreSQLStorage.get_session_dates / get_level_history) against a real database."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.adapters.storage.postgresql import PostgreSQLStorage
from backend.adapters.storage.postgresql_async import AsyncPostgreSQLStorage
from backend.domain.entities import GammaAggregate, MarketPrice
from backend.infrastructure.database.engine import create_engine, create_sync_engine
from backend.infrastructure.database.session import create_session_factory, create_sync_session_factory
from tests.test_postgresql_integration import _require_test_database_url

pytestmark = pytest.mark.integration

OPEN = datetime(2026, 10, 30, 13, 30, tzinfo=UTC)  # Friday 09:30 ET


@pytest.mark.asyncio
async def test_session_dates_and_level_history() -> None:
    url = _require_test_database_url()
    engine = create_sync_engine(url)
    storage = PostgreSQLStorage(create_sync_session_factory(engine))
    symbol = "ZR" + uuid4().hex[:6].upper()
    try:
        for at in (
            OPEN + timedelta(minutes=5),  # Friday, inside the session
            OPEN - timedelta(days=1) + timedelta(hours=6, minutes=29),  # Thursday 15:59 ET
            OPEN - timedelta(days=2) - timedelta(minutes=10),  # Wednesday 09:20 ET: pre-market only, not a session
        ):
            storage.save_market_price(MarketPrice(symbol=symbol, as_of=at, price=Decimal("100"), volume=0))
        for minute, flip, abs_strike in ((1, None, Decimal("0")), (3, Decimal("7600"), Decimal("7650"))):
            storage.save_gamma_aggregate(
                GammaAggregate(
                    symbol=symbol,
                    as_of=OPEN + timedelta(minutes=minute),
                    view="tactical",
                    call_wall=Decimal("7700"),
                    put_wall=None,
                    gamma_flip=flip,
                    absolute_gamma_strike=abs_strike,
                )
            )
        storage.save_gamma_aggregate(GammaAggregate(symbol=symbol, as_of=OPEN + timedelta(minutes=2), view="0dte", call_wall=Decimal("1")))

        async_engine = create_engine(url)
        try:
            reader = AsyncPostgreSQLStorage(create_session_factory(async_engine))
            days = await reader.get_session_dates(symbol, date(2026, 10, 25), date(2026, 11, 2))
            levels = await reader.get_level_history(symbol, OPEN, OPEN + timedelta(hours=6, minutes=30), "tactical")
        finally:
            await async_engine.dispose()

        assert days == [date(2026, 10, 30), date(2026, 10, 29)]
        assert [cycle.time for cycle in levels] == [OPEN + timedelta(minutes=1), OPEN + timedelta(minutes=3)]
        assert levels[0].absolute_gamma_strike is None and levels[0].gamma_flip is None and levels[0].put_wall is None
        assert (levels[1].call_wall, levels[1].gamma_flip, levels[1].absolute_gamma_strike) == (Decimal("7700"), Decimal("7600"), Decimal("7650"))
    finally:
        with engine.begin() as connection:
            for table in ("gamma_aggregate_items", "gamma_aggregates", "market_snapshots"):
                connection.execute(text(f"DELETE FROM {table} WHERE underlying_id IN (SELECT id FROM underlyings WHERE symbol = :s)"), {"s": symbol})
            connection.execute(text("DELETE FROM underlyings WHERE symbol = :s"), {"s": symbol})
        engine.dispose()
