from __future__ import annotations

from importlib import import_module

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from backend.core.container import build_container
from backend.adapters.storage.memory import InMemoryStorage
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
        lambda table, column, **kwargs: altered_columns.append(
            (table, column, kwargs)
        ),
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
    migration = import_module(
        "backend.db.migrations.0003_make_theta_vega_not_null"
    )
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
