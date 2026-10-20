from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.adapters.storage.postgresql import PostgreSQLStorage
from backend.adapters.storage.postgresql_async import AsyncPostgreSQLStorage
from backend.domain.entities import MinuteOptionFlow
from backend.infrastructure.database.engine import create_engine, create_sync_engine
from backend.infrastructure.database.session import create_session_factory, create_sync_session_factory
from tests.test_postgresql_integration import _require_test_database_url

pytestmark = pytest.mark.integration

MINUTE = datetime(2026, 10, 19, 14, 31, tzinfo=UTC)


@pytest.mark.asyncio
async def test_minute_flow_round_trip_adds_late_trades_and_sums_the_roots() -> None:
    url = _require_test_database_url()
    sync_engine = create_sync_engine(url)
    symbol = "ZMF" + uuid4().hex[:6].upper()
    try:
        # The table comes from migration 0048 (alembic upgrade head against the test database).
        async_engine = create_engine(url)
        try:
            storage = AsyncPostgreSQLStorage(create_session_factory(async_engine))
            await storage.save_minute_option_flow([])  # nothing to write is not an error
            await storage.save_minute_option_flow(
                [
                    MinuteOptionFlow(MINUTE, symbol, "SPXW", 10.5, -4.0, 3, 1, 12, 20),
                    MinuteOptionFlow(MINUTE, symbol, "SPX", 2.0, 1.0, 1, 0, 2, 2),
                    MinuteOptionFlow(MINUTE + timedelta(minutes=1), symbol, "SPXW", -3.0, 0.5, 1, 2, 4, 9),
                ]
            )
            # A late trade for an already flushed minute is ADDED to its row, not replacing it.
            await storage.save_minute_option_flow([MinuteOptionFlow(MINUTE, symbol, "SPXW", 1.5, -1.0, 1, 0, 3, 3)])
            minutes = await storage.get_minute_option_flow(symbol, MINUTE, MINUTE + timedelta(minutes=5))
            outside = await storage.get_minute_option_flow(symbol, MINUTE + timedelta(hours=1), MINUTE + timedelta(hours=2))
        finally:
            await async_engine.dispose()
        assert [row.minute for row in minutes] == [MINUTE, MINUTE + timedelta(minutes=1)]
        first = minutes[0]  # SPXW (10.5 + 1.5) + SPX (2.0), puts (-4 - 1) + 1
        assert (first.calls_delta_flow, first.puts_delta_flow) == (pytest.approx(14.0), pytest.approx(-4.0))
        assert (first.trades_classified, first.trades_unclassified, first.contracts_classified, first.contracts_total) == (5, 1, 17, 25)
        assert minutes[1].calls_delta_flow == pytest.approx(-3.0)
        assert outside == []
        with sync_engine.connect() as connection:
            rows = connection.execute(text("SELECT count(*) FROM minute_option_flow WHERE symbol = :s"), {"s": symbol}).scalar()
        assert rows == 3  # SPXW + SPX of the first minute, SPXW of the second
    finally:
        with sync_engine.begin() as connection:
            connection.execute(text("DELETE FROM minute_option_flow WHERE symbol = :s"), {"s": symbol})
        sync_engine.dispose()


def test_option_abs_deltas_read_the_recent_chain_snapshots_of_one_underlying() -> None:
    url = _require_test_database_url()
    sync_engine = create_sync_engine(url)
    try:
        storage = PostgreSQLStorage(create_sync_session_factory(sync_engine))
        # Unknown underlying: nothing, not an error.
        assert storage.get_latest_option_abs_deltas("ZZNOPE") == {}
    finally:
        sync_engine.dispose()
