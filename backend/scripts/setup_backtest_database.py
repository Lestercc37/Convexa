"""One-time (idempotent) setup for the isolated Convexa_backtest database --
Phase 0 of the historical-gamma backtest (see backtest_dry_run.py for Phase
"validate the ThetaData pull itself" and the eventual full backfill script
for the real multi-year run). Creates the database if it doesn't exist yet,
then runs every existing Alembic migration against it -- the exact same
migration files the live Convexa database uses, so the backtest schema never
drifts from what the live calculators/storage code actually expects.

Confirmed with the user, 2026-09-27: this backtest must never touch, and can
never collide with, the live Convexa database -- this script only ever
opens a connection to Postgres's own "postgres" maintenance database (to
issue the one CREATE DATABASE statement, which cannot run against a database
that doesn't exist yet, or one this same process already has open) and to
Convexa_backtest itself. It never opens a connection to, or reads a single
row from, the live Convexa database.

Usage: python -m backend.scripts.setup_backtest_database
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

import backend.core.settings as settings_module
from backend.scripts._backtest_db import (
    BACKTEST_DATABASE_NAME,
    admin_sync_url,
    backtest_async_url,
)

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ensure_database_exists() -> None:
    engine = create_engine(admin_sync_url(), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": BACKTEST_DATABASE_NAME},
            ).first()
            if exists:
                logger.info("%s already exists", BACKTEST_DATABASE_NAME)
                return
            # CREATE DATABASE cannot be parameterized (identifiers, not
            # values) -- BACKTEST_DATABASE_NAME is a fixed module constant,
            # never user input, so this is not a real injection surface.
            connection.execute(text(f'CREATE DATABASE "{BACKTEST_DATABASE_NAME}"'))
            logger.info("Created database %s", BACKTEST_DATABASE_NAME)
    finally:
        engine.dispose()


def _run_migrations() -> None:
    """Runs the app's own Alembic migrations against Convexa_backtest
    instead of the live database -- backend/db/env.py always reads
    get_settings().database_url itself (the same file the live app's own
    migrations use), so patching that one call for the duration of this
    single Alembic invocation is what redirects it, rather than
    duplicating env.py's migration-running logic for a second database.
    Restored immediately after, even on failure."""
    backtest_url_string = backtest_async_url().render_as_string(hide_password=False)
    original_get_settings = settings_module.get_settings

    class _BacktestSettings:
        database_url = backtest_url_string

    settings_module.get_settings = lambda: _BacktestSettings()  # type: ignore[assignment]
    try:
        config = Config(str(REPO_ROOT / "alembic.ini"))
        command.upgrade(config, "head")
    finally:
        settings_module.get_settings = original_get_settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5.5s %(message)s")
    _ensure_database_exists()
    _run_migrations()
    logger.info("%s is ready (schema at head).", BACKTEST_DATABASE_NAME)


if __name__ == "__main__":
    main()
