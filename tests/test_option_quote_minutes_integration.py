from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.adapters.storage.postgresql_async import (
    OPTION_QUOTE_MINUTES_BATCH,
    AsyncPostgreSQLStorage,
)
from backend.domain.entities import OptionQuoteMinute
from backend.infrastructure.database.engine import create_engine, create_sync_engine
from backend.infrastructure.database.session import create_session_factory
from tests.test_postgresql_integration import _require_test_database_url

pytestmark = pytest.mark.integration

MINUTE = datetime(2026, 10, 14, 14, 5, tzinfo=UTC)


@pytest.mark.asyncio
async def test_minute_rows_round_trip_ignore_duplicates_and_batch() -> None:
    url = _require_test_database_url()
    sync_engine = create_sync_engine(url)
    prefix = "ZQM" + uuid4().hex[:6].upper()
    try:
        # The table comes from migration 0040 (alembic upgrade head against the test database).
        rows = [
            OptionQuoteMinute(MINUTE, f"{prefix}C{index:08d}", Decimal("10.1000"), Decimal("10.5000"), MINUTE)
            for index in range(OPTION_QUOTE_MINUTES_BATCH + 25)
        ]
        async_engine = create_engine(url)
        try:
            storage = AsyncPostgreSQLStorage(create_session_factory(async_engine))
            await storage.save_option_quote_minutes([])  # nothing to write is not an error
            await storage.save_option_quote_minutes(rows)  # more than one batch
            await storage.save_option_quote_minutes(rows[:5])  # the same (time, occ_symbol) again: ignored
        finally:
            await async_engine.dispose()

        with sync_engine.connect() as connection:
            count = connection.execute(
                text("select count(*) from option_quote_minutes where occ_symbol like :p"), {"p": prefix + "%"}
            ).scalar()
            sample = connection.execute(
                text("select bid, ask, quote_time from option_quote_minutes where occ_symbol = :o and time = :t"),
                {"o": rows[0].occ_symbol, "t": MINUTE},
            ).one()
        assert count == len(rows)
        assert (sample[0], sample[1]) == (Decimal("10.1000"), Decimal("10.5000"))
        assert sample[2] == MINUTE
    finally:
        with sync_engine.begin() as connection:
            connection.execute(text("delete from option_quote_minutes where occ_symbol like :p"), {"p": prefix + "%"})
        sync_engine.dispose()
