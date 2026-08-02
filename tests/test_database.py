from __future__ import annotations

from importlib import import_module

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

import backend.core.container as container_module
from backend.adapters.storage.memory import InMemoryStorage
from backend.adapters.storage.postgresql import PostgreSQLStorage
from backend.core.container import build_container
from backend.core.settings import Settings
from backend.infrastructure.database.engine import create_engine
from backend.infrastructure.database.session import create_session_factory


@pytest.mark.asyncio
async def test_create_engine_returns_async_engine() -> None:
    engine = create_engine("sqlite+aiosqlite:///:memory:")

    try:
        assert isinstance(engine, AsyncEngine)
        assert str(engine.url) == "sqlite+aiosqlite:///:memory:"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_session_factory_creates_async_session() -> None:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = create_session_factory(engine)

    try:
        async with session_factory() as session:
            assert isinstance(session, AsyncSession)
            result = await session.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_container_registers_in_memory_storage_adapter() -> None:
    container = build_container()

    try:
        assert isinstance(container.storage, InMemoryStorage)
    finally:
        await container.database_engine.dispose()


@pytest.mark.asyncio
async def test_container_registers_postgresql_storage_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://user:password@localhost/convexa",
    )
    monkeypatch.setattr(container_module, "get_settings", lambda: settings)
    container = build_container()

    try:
        assert isinstance(container.storage, PostgreSQLStorage)
        assert container.storage_engine is not None
    finally:
        if container.storage_engine is not None:
            container.storage_engine.dispose()
        await container.database_engine.dispose()


def test_charm_vanna_migration_adds_mandatory_snapshot_columns(monkeypatch) -> None:
    migration = import_module(
        "backend.db.migrations.0002_add_charm_vanna_to_option_chain_snapshots"
    )
    added_columns = []
    altered_columns = []

    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda table, column: added_columns.append((table, column)),
    )
    monkeypatch.setattr(
        migration.op,
        "alter_column",
        lambda table, column, **kwargs: altered_columns.append((table, column, kwargs)),
    )

    migration.upgrade()

    assert [column.name for _, column in added_columns] == ["charm", "vanna"]
    assert all(table == "option_chain_snapshots" for table, _ in added_columns)
    assert all(column.nullable is False for _, column in added_columns)
    assert altered_columns == [
        ("option_chain_snapshots", "charm", {"server_default": None}),
        ("option_chain_snapshots", "vanna", {"server_default": None}),
    ]


def test_theta_vega_migration_backfills_before_not_null(monkeypatch) -> None:
    migration = import_module("backend.db.migrations.0003_make_theta_vega_not_null")
    operations: list[tuple[str, str]] = []
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: operations.append(("execute", str(statement))),
    )
    monkeypatch.setattr(
        migration.op,
        "alter_column",
        lambda table, column, **kwargs: operations.append(
            ("alter", f"{table}.{column}:{kwargs['nullable']}")
        ),
    )

    migration.upgrade()

    assert operations == [
        ("execute", "UPDATE option_chain_snapshots SET theta = 0 WHERE theta IS NULL"),
        ("execute", "UPDATE option_chain_snapshots SET vega = 0 WHERE vega IS NULL"),
        ("alter", "option_chain_snapshots.theta:False"),
        ("alter", "option_chain_snapshots.vega:False"),
    ]


def test_spot_price_migration_adds_mandatory_snapshot_column(monkeypatch) -> None:
    migration = import_module("backend.db.migrations.0004_add_spot_price")
    added_columns = []
    altered_columns = []
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda table, column: added_columns.append((table, column)),
    )
    monkeypatch.setattr(
        migration.op,
        "alter_column",
        lambda table, column, **kwargs: altered_columns.append((table, column, kwargs)),
    )

    migration.upgrade()

    assert len(added_columns) == 1
    table, column = added_columns[0]
    assert table == "option_chain_snapshots"
    assert column.name == "spot_price"
    assert column.nullable is False
    assert altered_columns == [("option_chain_snapshots", "spot_price", {"server_default": None})]


def test_daily_gamma_reference_migration_creates_one_row_per_day(monkeypatch) -> None:
    migration = import_module("backend.db.migrations.0005_create_daily_gamma_reference")
    statements: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: statements.append(str(statement)),
    )

    migration.upgrade()

    schema = " ".join(statements)
    assert "CREATE TABLE IF NOT EXISTS daily_gamma_reference" in schema
    assert "PRIMARY KEY (underlying_id, date)" in schema
    assert "pc_oi_ratio numeric NOT NULL" in schema
    assert "skew_25d numeric NOT NULL" in schema
