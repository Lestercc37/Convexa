from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.adapters.storage.memory import InMemoryStorage
from backend.adapters.storage.postgresql import PostgreSQLStorage
from backend.domain.entities import Underlying, UnderlyingKind
from backend.domain.underlyings import ACTIVE_UNDERLYINGS


def test_in_memory_storage_lists_all_active_underlyings() -> None:
    assert InMemoryStorage().list_underlyings() == sorted(
        ACTIVE_UNDERLYINGS, key=lambda underlying: underlying.symbol
    )


def test_active_underlying_classification() -> None:
    expected = {
        "SPY": Underlying("SPY", UnderlyingKind.EQUITY, True),
        "QQQ": Underlying("QQQ", UnderlyingKind.EQUITY, True),
        "IWM": Underlying("IWM", UnderlyingKind.EQUITY, True),
        "SPX": Underlying("SPX", UnderlyingKind.INDEX, True),
        "VIX": Underlying("VIX", UnderlyingKind.INDEX, True),
        "TSLA": Underlying("TSLA", UnderlyingKind.EQUITY, True),
        "NVDA": Underlying("NVDA", UnderlyingKind.EQUITY, True),
        "META": Underlying("META", UnderlyingKind.EQUITY, True),
        "AMZN": Underlying("AMZN", UnderlyingKind.EQUITY, True),
        "GOOGL": Underlying("GOOGL", UnderlyingKind.EQUITY, True),
    }

    assert {item.symbol: item for item in ACTIVE_UNDERLYINGS} == expected


def test_ensure_underlying_corrects_configured_symbol_metadata() -> None:
    session_factory, engine = _underlying_session_factory()
    try:
        with session_factory.begin() as session:
            session.execute(
                text(
                    """
                    INSERT INTO underlyings (symbol, kind, is_priority)
                    VALUES ('SPX', 'equity', false)
                    """
                )
            )
            PostgreSQLStorage._ensure_underlying(session, "spx")

        with session_factory() as session:
            row = session.execute(
                text(
                    """
                    SELECT symbol, kind, is_priority
                    FROM underlyings
                    WHERE symbol = 'SPX'
                    """
                )
            ).one()

        assert row == ("SPX", "index", True)
    finally:
        engine.dispose()


def test_ensure_underlying_preserves_unconfigured_symbol_metadata() -> None:
    session_factory, engine = _underlying_session_factory()
    try:
        with session_factory.begin() as session:
            session.execute(
                text(
                    """
                    INSERT INTO underlyings (symbol, kind, is_priority)
                    VALUES ('NDX', 'index', true)
                    """
                )
            )
            PostgreSQLStorage._ensure_underlying(session, "ndx")

        with session_factory() as session:
            row = session.execute(
                text(
                    """
                    SELECT symbol, kind, is_priority
                    FROM underlyings
                    WHERE symbol = 'NDX'
                    """
                )
            ).one()

        assert row == ("NDX", "index", True)
    finally:
        engine.dispose()


def _underlying_session_factory() -> tuple[sessionmaker[Session], Engine]:
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
    return sessionmaker(engine), engine
