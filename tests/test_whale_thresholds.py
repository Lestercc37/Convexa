from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, text

from backend.adapters.providers.mock import MockDataProvider
from backend.adapters.storage.memory import InMemoryStorage
from backend.adapters.storage.postgresql import PostgreSQLStorage
from backend.core.container import build_whale_alerts_engine
from backend.domain.entities import OptionChain, WhaleThreshold
from backend.domain.underlyings import ACTIVE_UNDERLYINGS
from backend.domain.use_cases import WhaleAlert, WhaleAlertType
from backend.infrastructure.database.session import create_sync_session_factory


def _chain(base: OptionChain, volume: int, period: int) -> OptionChain:
    return replace(
        base,
        as_of=datetime(2026, 8, 6, 14, 30, tzinfo=UTC)
        + timedelta(minutes=period),
        contracts=(replace(base.contracts[0], volume=volume, last=Decimal("1.00")),),
    )


def _prime_and_process(
    symbol: str, storage: InMemoryStorage, final_delta: int
) -> tuple[WhaleAlert, ...]:
    engine = build_whale_alerts_engine(storage)
    base = MockDataProvider().get_option_chain(symbol)
    cumulative = 100
    engine.process(_chain(base, cumulative, 0))
    for period in range(1, 6):
        cumulative += 100
        engine.process(_chain(base, cumulative, period))
    cumulative += final_delta
    engine.process(_chain(base, cumulative, 6))
    # A 1-minute bucket is only finalized (classified, pushed into the
    # window) once a reading from the *next* minute arrives — same
    # inherent latency as any bucket/candle aggregation. This extra call
    # (zero further delta) is what finalizes period 6's bucket.
    return engine.process(_chain(base, cumulative, 7))


def test_engine_uses_persisted_symbol_thresholds() -> None:
    storage = InMemoryStorage()
    storage.save_whale_threshold(
        WhaleThreshold(
            symbol="SPX",
            unusual_min=Decimal("20000"),
            whale_min=Decimal("100000"),
            unusual_multiplier=Decimal("2"),
            whale_multiplier=Decimal("5"),
            sustained_flow_min=Decimal("500000"),
        )
    )

    alerts = _prime_and_process("SPX", storage, final_delta=250)

    assert len(alerts) == 1
    assert alerts[0].alert_type is WhaleAlertType.UNUSUAL
    assert alerts[0].amount == Decimal("25000.00")


def test_engine_uses_defaults_when_symbol_has_no_threshold_row() -> None:
    storage = InMemoryStorage()

    alerts = _prime_and_process("NEW", storage, final_delta=250)

    assert alerts == ()


def test_memory_storage_seeds_all_active_symbols() -> None:
    thresholds = InMemoryStorage().get_whale_thresholds()

    assert set(thresholds) == {
        underlying.symbol for underlying in ACTIVE_UNDERLYINGS
    }
    assert all(
        threshold.unusual_min == Decimal("40000")
        and threshold.whale_min == Decimal("150000")
        and threshold.unusual_multiplier == Decimal("3.0")
        and threshold.whale_multiplier == Decimal("6.0")
        and threshold.sustained_flow_min == Decimal("500000")
        for threshold in thresholds.values()
    )


def test_postgresql_storage_reads_and_writes_whale_thresholds() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE underlyings (
                    id integer PRIMARY KEY AUTOINCREMENT,
                    symbol text NOT NULL UNIQUE,
                    kind text NOT NULL,
                    is_priority boolean NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE whale_thresholds (
                    underlying_id integer PRIMARY KEY REFERENCES underlyings(id),
                    unusual_min numeric NOT NULL,
                    whale_min numeric NOT NULL,
                    unusual_multiplier numeric NOT NULL,
                    whale_multiplier numeric NOT NULL,
                    sustained_flow_min numeric NOT NULL
                )
                """
            )
        )
    storage = PostgreSQLStorage(create_sync_session_factory(engine))
    threshold = WhaleThreshold(
        symbol="SPX",
        unusual_min=Decimal("25000"),
        whale_min=Decimal("125000"),
        unusual_multiplier=Decimal("2.5"),
        whale_multiplier=Decimal("5.5"),
        sustained_flow_min=Decimal("500000"),
    )

    storage.save_whale_threshold(threshold)

    assert storage.get_whale_thresholds() == {"SPX": threshold}
    engine.dispose()
