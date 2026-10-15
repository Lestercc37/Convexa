from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.adapters.storage.postgresql import PostgreSQLStorage
from backend.domain.entities import GammaAggregate, GammaAggregateItem
from backend.infrastructure.database.engine import create_sync_engine
from backend.infrastructure.database.session import create_sync_session_factory
from tests.test_postgresql_integration import _require_test_database_url

pytestmark = pytest.mark.integration

AS_OF = datetime(2026, 10, 15, 14, 30, tzinfo=UTC)


def item(strike: int, **extra) -> GammaAggregateItem:
    return GammaAggregateItem(
        strike=Decimal(strike),
        total_gamma_exposure=Decimal(10),
        call_gamma_exposure=Decimal(6),
        put_gamma_exposure=Decimal(-4),
        net_gamma=Decimal(2),
        contract_count=2,
        absolute_gamma=Decimal(2),
        open_interest=100,
        volume=7,
        **extra,
    )


def test_delta_vanna_charm_round_trip_and_old_rows_read_as_not_stored() -> None:
    engine = create_sync_engine(_require_test_database_url())
    storage = PostgreSQLStorage(create_sync_session_factory(engine))
    symbol = "ZG" + uuid4().hex[:6].upper()
    try:
        storage.save_gamma_aggregate(
            GammaAggregate(
                symbol=symbol,
                as_of=AS_OF,
                view="tactical",
                items=(
                    item(7800, delta_exposure=Decimal("1234.5"), vanna_exposure=Decimal("-77.25"), charm_exposure=Decimal("9.5")),
                    item(7805),  # an item without the new fields: stored as NULL, never as 0
                ),
            )
        )

        loaded = storage.get_latest_gamma_aggregate(symbol, view="tactical")

        assert loaded is not None
        by_strike = {entry.strike: entry for entry in loaded.items}
        assert by_strike[Decimal(7800)].delta_exposure == Decimal("1234.5")
        assert by_strike[Decimal(7800)].vanna_exposure == Decimal("-77.25")
        assert by_strike[Decimal(7800)].charm_exposure == Decimal("9.5")
        assert by_strike[Decimal(7805)].delta_exposure is None
        assert by_strike[Decimal(7805)].vanna_exposure is None
        assert by_strike[Decimal(7805)].charm_exposure is None

        # A second save of the same cycle updates the new columns (ON CONFLICT DO UPDATE), not just inserts.
        storage.save_gamma_aggregate(
            GammaAggregate(symbol=symbol, as_of=AS_OF, view="tactical", items=(item(7800, delta_exposure=Decimal(5)),))
        )
        reloaded = storage.get_latest_gamma_aggregate(symbol, view="tactical")
        assert reloaded is not None
        assert {entry.strike: entry for entry in reloaded.items}[Decimal(7800)].delta_exposure == Decimal(5)
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM gamma_aggregate_items WHERE underlying_id IN (SELECT id FROM underlyings WHERE symbol = :s)"),
                {"s": symbol},
            )
            connection.execute(
                text("DELETE FROM gamma_aggregates WHERE underlying_id IN (SELECT id FROM underlyings WHERE symbol = :s)"),
                {"s": symbol},
            )
            connection.execute(text("DELETE FROM underlyings WHERE symbol = :s"), {"s": symbol})
        engine.dispose()
