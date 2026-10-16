from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.adapters.storage.postgresql_async import LARGE_TRADE_PRINTS_BATCH, AsyncPostgreSQLStorage
from backend.domain.entities import LargeTradePrint
from backend.infrastructure.database.engine import create_engine, create_sync_engine
from backend.infrastructure.database.session import create_session_factory
from tests.test_postgresql_integration import _require_test_database_url

pytestmark = pytest.mark.integration

WHEN = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_large_prints_round_trip_with_their_nulls_and_batch() -> None:
    url = _require_test_database_url()
    sync_engine = create_sync_engine(url)
    symbol = "ZLP" + uuid4().hex[:6].upper()
    try:
        # The table comes from migration 0043 (alembic upgrade head against the test database).
        rows = [
            LargeTradePrint(symbol, WHEN, Decimal("771.8000"), 126_512 + index, 124, 57, False, WHEN)
            for index in range(LARGE_TRADE_PRINTS_BATCH + 25)
        ]
        rows.append(LargeTradePrint(symbol, WHEN, Decimal("765.1500"), 1_466_700, None, None, None, WHEN))
        async_engine = create_engine(url)
        try:
            storage = AsyncPostgreSQLStorage(create_session_factory(async_engine))
            await storage.save_large_trade_prints([])  # nothing to write is not an error
            await storage.save_large_trade_prints(rows)  # more than one batch
        finally:
            await async_engine.dispose()
        with sync_engine.connect() as connection:
            total = connection.execute(text("SELECT count(*) FROM large_trade_prints WHERE symbol = :s"), {"s": symbol}).scalar()
            unknown = connection.execute(
                text("SELECT condition, exchange, at_market FROM large_trade_prints WHERE symbol = :s AND size = 1466700"), {"s": symbol}
            ).one()
        assert total == len(rows)
        assert tuple(unknown) == (None, None, None)
    finally:
        with sync_engine.begin() as connection:
            connection.execute(text("DELETE FROM large_trade_prints WHERE symbol = :s"), {"s": symbol})
        sync_engine.dispose()
