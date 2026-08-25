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
from backend.infrastructure.database.engine import create_sync_engine
from backend.infrastructure.database.session import create_sync_session_factory

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
        for table_name in ("gamma_aggregates", "market_snapshots"):
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
