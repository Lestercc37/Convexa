"""Shared helper for the historical-gamma backtest scripts (setup_backtest_
database.py, backtest_dry_run.py, and the eventual full backfill) -- the one
place that knows how to point at the isolated Convexa_backtest database, so
none of those scripts ever constructs, prints, or logs the real connection
string themselves.

Convexa_backtest is a separate Postgres database on the same server as the
live Convexa database -- confirmed with the user, 2026-09-27: this research
backtest must never touch, and can never accidentally collide with, anything
the live scheduler/dashboard reads or writes. Same Postgres instance, same
credentials (reused from the app's own settings, never re-typed here), only
the database name differs.
"""

from __future__ import annotations

from sqlalchemy import make_url
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from backend.core.settings import get_settings
from backend.infrastructure.database.engine import create_sync_engine

BACKTEST_DATABASE_NAME = "Convexa_backtest"


def _live_url() -> URL:
    return make_url(get_settings().database_url)


def admin_sync_url() -> URL:
    """The server's own "postgres" maintenance database, psycopg-flavored
    -- the only database guaranteed to already exist, needed to issue
    CREATE DATABASE itself (which cannot run against the database it's in
    the middle of creating, nor against a database with other open
    connections from this same process)."""
    return _live_url().set(drivername="postgresql+psycopg", database="postgres")


def backtest_async_url() -> URL:
    """asyncpg-flavored URL -- what backend/db/env.py's Alembic migrations
    (async_engine_from_config) expect, same driver the live app's own
    DATABASE_URL uses."""
    return _live_url().set(database=BACKTEST_DATABASE_NAME)


def backtest_sync_url() -> URL:
    """psycopg-flavored URL -- what the synchronous PostgreSQLStorage (used
    by the backfill/dry-run scripts themselves, same as every other
    one-off script in this package) needs."""
    return _live_url().set(drivername="postgresql+psycopg", database=BACKTEST_DATABASE_NAME)


def backtest_session_factory() -> sessionmaker[Session]:
    engine = create_sync_engine(backtest_sync_url().render_as_string(hide_password=False))
    return sessionmaker(engine)
