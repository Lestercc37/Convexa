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
        "NDX": Underlying("NDX", UnderlyingKind.INDEX, True),
        "TSLA": Underlying("TSLA", UnderlyingKind.EQUITY, True),
        "NVDA": Underlying("NVDA", UnderlyingKind.EQUITY, True),
        "META": Underlying("META", UnderlyingKind.EQUITY, True),
        "AMZN": Underlying("AMZN", UnderlyingKind.EQUITY, True),
        "GOOGL": Underlying("GOOGL", UnderlyingKind.EQUITY, True),
        "AAPL": Underlying("AAPL", UnderlyingKind.EQUITY, True),
        "MSFT": Underlying("MSFT", UnderlyingKind.EQUITY, True),
        "DIA": Underlying("DIA", UnderlyingKind.EQUITY, True),
        "ES": Underlying("ES", UnderlyingKind.FUTURE, True),
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


def test_ensure_underlying_classifies_es_as_future() -> None:
    session_factory, engine = _underlying_session_factory()
    try:
        with session_factory.begin() as session:
            PostgreSQLStorage._ensure_underlying(session, "es")

        with session_factory() as session:
            row = session.execute(
                text(
                    """
                    SELECT symbol, kind, is_priority
                    FROM underlyings
                    WHERE symbol = 'ES'
                    """
                )
            ).one()

        assert row == ("ES", "future", True)
    finally:
        engine.dispose()


def test_ensure_underlying_preserves_unconfigured_symbol_metadata() -> None:
    # XOM, not AAPL -- AAPL joined ACTIVE_UNDERLYINGS (see
    # test_active_underlying_classification above), so it's no longer a
    # genuinely unconfigured symbol; using it here would still pass (its
    # configured kind/is_priority happen to match what this test
    # pre-inserts) but would no longer test what this test claims to.
    # Pick a new placeholder, not one of the currently-configured
    # symbols, the next time a real one gets added here too.
    session_factory, engine = _underlying_session_factory()
    try:
        with session_factory.begin() as session:
            session.execute(
                text(
                    """
                    INSERT INTO underlyings (symbol, kind, is_priority)
                    VALUES ('XOM', 'equity', true)
                    """
                )
            )
            PostgreSQLStorage._ensure_underlying(session, "xom")

        with session_factory() as session:
            row = session.execute(
                text(
                    """
                    SELECT symbol, kind, is_priority
                    FROM underlyings
                    WHERE symbol = 'XOM'
                    """
                )
            ).one()

        assert row == ("XOM", "equity", True)
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
