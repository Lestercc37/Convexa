from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from backend.adapters.providers.mock.provider import MockDataProvider
from backend.adapters.storage.postgresql import PostgreSQLStorage
from backend.adapters.storage.postgresql_async import AsyncPostgreSQLStorage
from backend.core.settings import Settings
from backend.domain.entities import (
    AggressorSide,
    DailyBar,
    DailyGammaReference,
    ExposureLeadersSettings,
    FlowEvent,
    FlowEventType,
    GammaAggregate,
    GammaAggregateItem,
    MarketPrice,
    NegativeGammaBoardSettings,
    ScreenerPreset,
    WhaleThreshold,
)
from backend.domain.underlyings import ACTIVE_UNDERLYINGS
from backend.domain.use_cases.flow import WhaleAlert, WhaleAlertType
from backend.infrastructure.database.engine import create_engine, create_sync_engine
from backend.infrastructure.database.session import create_session_factory, create_sync_session_factory

pytestmark = pytest.mark.integration


@pytest.fixture
def postgresql_storage() -> Iterator[tuple[PostgreSQLStorage, Engine, str]]:
    settings = Settings(_env_file=".env")
    if not settings.database_url.startswith("postgresql"):
        pytest.skip("DATABASE_URL does not point to PostgreSQL")

    engine = create_sync_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        engine.dispose()
        pytest.skip(f"PostgreSQL is unavailable: {type(exc).__name__}")

    symbol = f"T{uuid4().hex[:7]}".upper()
    storage = PostgreSQLStorage(create_sync_session_factory(engine))
    try:
        yield storage, engine, symbol
    finally:
        _delete_test_data(engine, symbol)
        engine.dispose()


def test_option_chain_round_trip_against_postgresql(
    postgresql_storage: tuple[PostgreSQLStorage, Engine, str],
) -> None:
    storage, _, symbol = postgresql_storage
    source = MockDataProvider().get_option_chain(symbol)

    storage.save_chain_snapshot(source)
    loaded = storage.get_latest_chain_snapshot(symbol)

    assert loaded is not None
    assert loaded.symbol == symbol
    assert loaded.as_of == source.as_of
    assert loaded.spot_price == source.spot_price
    assert loaded.contracts == source.contracts


def test_gamma_aggregate_round_trip_against_postgresql(
    postgresql_storage: tuple[PostgreSQLStorage, Engine, str],
) -> None:
    storage, _, symbol = postgresql_storage
    aggregate = GammaAggregate(
        symbol=symbol,
        as_of=datetime.now(timezone.utc),
        # Hand-built per-strike breakdown — the engine already computes this
        # on every calculation cycle; it was just discarded before this
        # point. Confirms the full save -> read round trip preserves it.
        items=(
            GammaAggregateItem(
                strike=Decimal("545"),
                total_gamma_exposure=Decimal("390"),
                call_gamma_exposure=Decimal("240"),
                put_gamma_exposure=Decimal("-150"),
                net_gamma=Decimal("90"),
                contract_count=2,
                absolute_gamma=Decimal("90"),
                open_interest=14000,
                volume=6800,
            ),
            GammaAggregateItem(
                strike=Decimal("550"),
                total_gamma_exposure=Decimal("200"),
                call_gamma_exposure=Decimal("120"),
                put_gamma_exposure=Decimal("-80"),
                net_gamma=Decimal("40"),
                contract_count=3,
                absolute_gamma=Decimal("40"),
            ),
        ),
        gamma_flip=Decimal("551.5"),
        call_wall=Decimal("555"),
        put_wall=Decimal("545"),
        max_pain=Decimal("550"),
        net_gamma=Decimal("-1250000"),
        dealer_gamma_notional=Decimal("-125000000"),
        vega_exposure=Decimal("875000"),
        theta_exposure=Decimal("-420000"),
        charm_exposure=Decimal("125000"),
        vanna_exposure=Decimal("250000"),
        absolute_gamma_strike=Decimal("550"),
        # Deliberately non-zero and each distinct from the others and
        # from every other field above -- these 4 used to silently come
        # back as 0 from PostgreSQLStorage regardless of what was saved
        # (missing columns), which a fixture leaving them at their
        # Decimal("0") dataclass default would never have caught.
        total_market_gamma=Decimal("-1250000"),
        positive_gamma=Decimal("130"),
        negative_gamma=Decimal("-40"),
        peak_gamma_value=Decimal("90"),
    )

    storage.save_gamma_aggregate(aggregate)
    loaded = storage.get_latest_gamma_aggregate(symbol)
    history = storage.get_gamma_history(
        symbol,
        aggregate.as_of - timedelta(seconds=1),
        aggregate.as_of + timedelta(seconds=1),
    )

    assert loaded == aggregate
    assert loaded is not None and loaded.items == aggregate.items
    # get_gamma_history intentionally does not reconstruct items — nothing
    # reads them from historical rows, only from the latest snapshot.
    assert history == [replace(aggregate, items=())]


def test_gamma_flip_none_round_trips_against_postgresql_as_null_not_zero(
    postgresql_storage: tuple[PostgreSQLStorage, Engine, str],
) -> None:
    """gamma_flip=None (no sign crossing found -- see GammaAggregate's own
    field comment) must come back as None, not silently become 0 --
    confirmed against the real database, not memory. gamma_aggregates.
    gamma_flip used to be NOT NULL; this is the real column the migration
    changed."""
    storage, _, symbol = postgresql_storage
    aggregate = GammaAggregate(
        symbol=symbol,
        as_of=datetime.now(timezone.utc),
        gamma_flip=None,
        net_gamma=Decimal("100"),
    )

    storage.save_gamma_aggregate(aggregate)
    loaded = storage.get_latest_gamma_aggregate(symbol)

    assert loaded is not None
    assert loaded.gamma_flip is None


def test_flow_event_round_trip_against_postgresql(
    postgresql_storage: tuple[PostgreSQLStorage, Engine, str],
) -> None:
    storage, _, symbol = postgresql_storage
    chain = MockDataProvider().get_option_chain(symbol)
    storage.save_chain_snapshot(chain)
    event = FlowEvent(
        symbol=symbol,
        occ_symbol=chain.contracts[0].occ_symbol,
        as_of=datetime.now(timezone.utc),
        event_type=FlowEventType.UNUSUAL,
        premium=Decimal("750000"),
        size=125,
        aggressor_side=AggressorSide.BUY,
    )

    storage.save_flow_event(event)
    loaded = storage.get_flow_events(symbol)

    assert loaded == [event]
    assert storage.get_recent_flow(symbol) == [event]


def test_market_price_and_underlying_round_trip_against_postgresql(
    postgresql_storage: tuple[PostgreSQLStorage, Engine, str],
) -> None:
    storage, _, symbol = postgresql_storage
    price = MarketPrice(
        symbol=symbol,
        as_of=datetime.now(timezone.utc),
        price=Decimal("552.25"),
        volume=1_250_000,
    )

    storage.save_market_price(price)

    assert storage.get_latest_price(symbol) == price
    assert any(item.symbol == symbol for item in storage.list_underlyings())
    history = storage.get_price_history(
        symbol,
        price.as_of - timedelta(seconds=1),
        price.as_of + timedelta(seconds=1),
    )
    assert history == [price]


def test_daily_bar_round_trip_against_postgresql(
    postgresql_storage: tuple[PostgreSQLStorage, Engine, str],
) -> None:
    storage, _, symbol = postgresql_storage
    bar = DailyBar(
        symbol=symbol,
        date=date(2026, 1, 2),
        open_price=Decimal("100.00"),
        high=Decimal("101.00"),
        low=Decimal("99.00"),
        close=Decimal("100.50"),
    )

    storage.save_daily_bar(bar)

    assert storage.get_daily_bars(symbol) == [bar]


def test_active_underlyings_are_seeded_in_postgresql(
    postgresql_storage: tuple[PostgreSQLStorage, Engine, str],
) -> None:
    storage, _, _ = postgresql_storage
    active_symbols = {underlying.symbol for underlying in ACTIVE_UNDERLYINGS}
    stored_active = {
        underlying.symbol: underlying
        for underlying in storage.list_underlyings()
        if underlying.symbol in active_symbols
    }

    assert stored_active == {
        underlying.symbol: underlying for underlying in ACTIVE_UNDERLYINGS
    }


def test_whale_threshold_round_trip_against_postgresql(
    postgresql_storage: tuple[PostgreSQLStorage, Engine, str],
) -> None:
    storage, _, symbol = postgresql_storage
    threshold = WhaleThreshold(
        symbol=symbol,
        unusual_min=Decimal("25000"),
        whale_min=Decimal("125000"),
        unusual_multiplier=Decimal("2.5"),
        whale_multiplier=Decimal("5.5"),
        sustained_flow_min=Decimal("500000"),
    )

    storage.save_whale_threshold(threshold)

    assert storage.get_whale_thresholds()[symbol] == threshold


def test_screener_preset_settings_round_trip_against_postgresql(
    postgresql_storage: tuple[PostgreSQLStorage, Engine, str],
) -> None:
    # Unlike other fixtures, this table is keyed by preset name, not by
    # the per-test-unique `symbol` — there is nothing to isolate this
    # test with, so restore the migration's seeded defaults afterward.
    storage, _, _ = postgresql_storage
    original_gamma = storage.get_screener_preset_settings(ScreenerPreset.NEGATIVE_GAMMA_BOARD)
    original_vanna = storage.get_screener_preset_settings(ScreenerPreset.VANNA_EXPOSURE_LEADERS)
    try:
        gamma_settings = NegativeGammaBoardSettings(net_gamma_max=Decimal("-25"))
        vanna_settings = ExposureLeadersSettings(min_magnitude=Decimal("1500"), limit=5)

        storage.save_screener_preset_settings(ScreenerPreset.NEGATIVE_GAMMA_BOARD, gamma_settings)
        storage.save_screener_preset_settings(
            ScreenerPreset.VANNA_EXPOSURE_LEADERS, vanna_settings
        )

        assert (
            storage.get_screener_preset_settings(ScreenerPreset.NEGATIVE_GAMMA_BOARD)
            == gamma_settings
        )
        assert (
            storage.get_screener_preset_settings(ScreenerPreset.VANNA_EXPOSURE_LEADERS)
            == vanna_settings
        )
    finally:
        if original_gamma is not None:
            storage.save_screener_preset_settings(
                ScreenerPreset.NEGATIVE_GAMMA_BOARD, original_gamma
            )
        if original_vanna is not None:
            storage.save_screener_preset_settings(
                ScreenerPreset.VANNA_EXPOSURE_LEADERS, original_vanna
            )


def test_daily_gamma_reference_upsert_and_read_against_postgresql(
    postgresql_storage: tuple[PostgreSQLStorage, Engine, str],
) -> None:
    storage, _, symbol = postgresql_storage
    original = DailyGammaReference(
        date=date(2026, 8, 1),
        symbol=symbol,
        net_gamma=Decimal("100"),
        pc_oi_ratio=Decimal("1.10"),
        skew_25d=Decimal("0.03"),
        atm_iv=Decimal("0.20"),
    )
    replacement = DailyGammaReference(
        date=original.date,
        symbol=symbol,
        net_gamma=Decimal("125"),
        pc_oi_ratio=Decimal("1.20"),
        skew_25d=Decimal("0.04"),
        atm_iv=Decimal("0.25"),
    )

    storage.save_daily_gamma_reference(original)
    storage.save_daily_gamma_reference(replacement)

    assert storage.get_daily_gamma_references(symbol) == [replacement]


@pytest.mark.asyncio
async def test_async_postgresql_storage_reads_what_the_sync_storage_wrote(
    postgresql_storage: tuple[PostgreSQLStorage, Engine, str],
) -> None:
    """AsyncPostgreSQLStorage (backing /gamma/{symbol} and /market/{symbol}
    since the threadpool-contention fix) is hand-written SQL, not
    generated from the sync PostgreSQLStorage queries it mirrors -- this
    is the one place that actually exercises it against a real Postgres,
    since the API test suite's InMemoryStorage-backed TestClient never
    touches it (see SyncStorageAsyncReadAdapter's own docstring)."""
    storage, engine, symbol = postgresql_storage
    now = datetime.now(timezone.utc)

    price = MarketPrice(symbol=symbol, as_of=now, price=Decimal("552.25"), volume=1_250_000)
    storage.save_market_price(price)
    gamma = GammaAggregate(
        symbol=symbol,
        as_of=now,
        gamma_flip=Decimal("550"),
        call_wall=Decimal("560"),
        put_wall=Decimal("540"),
        absolute_gamma_strike=Decimal("555"),
        net_gamma=Decimal("100"),
    )
    storage.save_gamma_aggregate(gamma)
    chain = MockDataProvider().get_option_chain(symbol)
    storage.save_chain_snapshot(chain)
    bar = DailyBar(
        symbol=symbol,
        date=date(2026, 1, 2),
        open_price=Decimal("100.00"),
        high=Decimal("101.00"),
        low=Decimal("99.00"),
        close=Decimal("100.50"),
    )
    storage.save_daily_bar(bar)
    reference = DailyGammaReference(
        date=date(2026, 8, 1),
        symbol=symbol,
        net_gamma=Decimal("100"),
        pc_oi_ratio=Decimal("1.10"),
        skew_25d=Decimal("0.03"),
        atm_iv=Decimal("0.20"),
    )
    storage.save_daily_gamma_reference(reference)

    async_engine = create_engine(Settings(_env_file=".env").database_url)
    try:
        async_storage = AsyncPostgreSQLStorage(create_session_factory(async_engine))

        assert await async_storage.get_latest_price(symbol) == price
        assert await async_storage.get_latest_gamma_aggregate(symbol) == gamma
        loaded_chain = await async_storage.get_latest_chain_snapshot(symbol)
        assert loaded_chain is not None
        assert loaded_chain.symbol == symbol
        assert loaded_chain.spot_price == chain.spot_price
        assert loaded_chain.contracts == chain.contracts
        assert await async_storage.get_daily_bars(symbol) == [bar]
        assert await async_storage.get_daily_gamma_references(symbol) == [reference]
        history = await async_storage.get_price_history(
            symbol, now - timedelta(seconds=1), now + timedelta(seconds=1)
        )
        assert history == [price]
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_async_postgresql_storage_save_market_price_round_trips(
    postgresql_storage: tuple[PostgreSQLStorage, Engine, str],
) -> None:
    """AsyncPostgreSQLStorage.save_market_price -- the write
    StreamUnderlyingPriceUseCase now uses instead of blocking the event
    loop with the old synchronous IStorage.save_market_price call
    (confirmed live, 2026-09; see that use case's own docstring). Writes
    via the async storage, reads back via the sync one, to prove the two
    are talking to the same table with the same schema, not just that
    each independently parses its own writes correctly."""
    sync_storage, _, symbol = postgresql_storage
    now = datetime.now(timezone.utc)
    price = MarketPrice(symbol=symbol, as_of=now, price=Decimal("328.50"), volume=2_000_000)

    async_engine = create_engine(Settings(_env_file=".env").database_url)
    try:
        async_storage = AsyncPostgreSQLStorage(create_session_factory(async_engine))
        await async_storage.save_market_price(price)

        assert sync_storage.get_latest_price(symbol) == price
        assert await async_storage.get_latest_price(symbol) == price
    finally:
        await async_engine.dispose()


def test_whale_alert_save_and_get_recent_against_postgresql(
    postgresql_storage: tuple[PostgreSQLStorage, Engine, str],
) -> None:
    """whale_alerts -- new this phase, dual-written alongside
    WhaleAlertsEngine's in-memory _alerts (see flow.py's _emit).
    Confirms the sync PostgreSQLStorage side of that persistence
    against a real table: round-trips every field, including
    alert_type (stored as the CHECK-constrained text column the
    migration defines), and that ordering is newest-first."""
    storage, _, symbol = postgresql_storage
    older = WhaleAlert(
        symbol=symbol,
        occ_symbol=f"{symbol}260220C00540000",
        alert_type=WhaleAlertType.UNUSUAL,
        amount=Decimal("45000"),
        as_of=datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc),
        estimated_buy_volume=Decimal("22500"),
        estimated_sell_volume=Decimal("22500"),
    )
    newer = WhaleAlert(
        symbol=symbol,
        occ_symbol=f"{symbol}260220P00540000",
        alert_type=WhaleAlertType.SUSTAINED_FLOW,
        amount=Decimal("625220.5"),
        as_of=datetime(2026, 8, 3, 14, 5, tzinfo=timezone.utc),
        estimated_buy_volume=Decimal("300000"),
        estimated_sell_volume=Decimal("325220.5"),
    )

    storage.save_whale_alert(older)
    storage.save_whale_alert(newer)

    assert storage.get_recent_whale_alerts(symbol) == [newer, older]
    assert storage.get_recent_whale_alerts(symbol, limit=1) == [newer]


@pytest.mark.asyncio
async def test_async_postgresql_storage_get_recent_whale_alerts_reads_what_the_sync_storage_wrote(
    postgresql_storage: tuple[PostgreSQLStorage, Engine, str],
) -> None:
    """AsyncPostgreSQLStorage.get_recent_whale_alerts -- built this
    phase alongside the sync write, on the same async pattern as
    /gamma and /market, ahead of /alerts and the screener actually
    being migrated to read it (not yet approved)."""
    sync_storage, _, symbol = postgresql_storage
    alert = WhaleAlert(
        symbol=symbol,
        occ_symbol=f"{symbol}260220C00540000",
        alert_type=WhaleAlertType.WHALE,
        amount=Decimal("210000"),
        as_of=datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc),
        estimated_buy_volume=Decimal("150000"),
        estimated_sell_volume=Decimal("60000"),
    )
    sync_storage.save_whale_alert(alert)

    async_engine = create_engine(Settings(_env_file=".env").database_url)
    try:
        async_storage = AsyncPostgreSQLStorage(create_session_factory(async_engine))
        assert await async_storage.get_recent_whale_alerts(symbol) == [alert]
    finally:
        await async_engine.dispose()


def _delete_test_data(engine: Engine, symbol: str) -> None:
    with engine.begin() as connection:
        underlying_id = connection.execute(
            text("SELECT id FROM underlyings WHERE symbol = :symbol"),
            {"symbol": symbol},
        ).scalar_one_or_none()
        if underlying_id is None:
            return
        connection.execute(
            text("DELETE FROM whale_thresholds WHERE underlying_id = :id"),
            {"id": underlying_id},
        )
        connection.execute(
            text("DELETE FROM daily_gamma_reference WHERE underlying_id = :id"),
            {"id": underlying_id},
        )
        connection.execute(
            text("DELETE FROM daily_bars WHERE underlying_id = :id"),
            {"id": underlying_id},
        )
        connection.execute(
            text(
                """
                DELETE FROM flow_events
                WHERE contract_id IN (
                    SELECT id FROM option_contracts WHERE underlying_id = :id
                )
                """
            ),
            {"id": underlying_id},
        )
        connection.execute(
            text(
                """
                DELETE FROM option_chain_snapshots
                WHERE contract_id IN (
                    SELECT id FROM option_contracts WHERE underlying_id = :id
                )
                """
            ),
            {"id": underlying_id},
        )
        connection.execute(
            text("DELETE FROM gamma_aggregate_items WHERE underlying_id = :id"),
            {"id": underlying_id},
        )
        for table_name in ("gamma_aggregates", "market_snapshots", "whale_alerts"):
            connection.execute(
                text(f"DELETE FROM {table_name} WHERE underlying_id = :id"),
                {"id": underlying_id},
            )
        connection.execute(
            text("DELETE FROM option_contracts WHERE underlying_id = :id"),
            {"id": underlying_id},
        )
        connection.execute(
            text("DELETE FROM underlyings WHERE id = :id"),
            {"id": underlying_id},
        )
