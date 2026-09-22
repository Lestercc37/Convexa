from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# A connection whose owning process dies mid-transaction (confirmed live,
# 2026-09-22: a `Stop-Process -Force` restart of the API left one PID
# "idle in transaction" holding a lock on `underlyings`/`option_contracts`
# -- Postgres has no way to know the client is gone, so the lock never
# clears on its own) otherwise sits forever, queuing every later writer
# behind it until *someone* manually finds and pg_terminate_backend()s it
# -- which is exactly what happened live today, cascading into GET
# /chain/{symbol} hanging 90+ seconds with no error, no timeout, nothing
# in the app's own logs to point at the real cause. This makes Postgres
# itself the backstop: any session that goes idle *inside* a still-open
# transaction for longer than this is killed server-side, regardless of
# why the client never committed/rolled back (a force-killed process, a
# crash, a bug) -- 5 minutes is generous enough to never interrupt any
# real unit of work this codebase does (every write here is a single
# fast upsert/insert, never a long-running interactive transaction).
IDLE_IN_TRANSACTION_TIMEOUT_MS = 5 * 60 * 1000


def create_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Create the application AsyncEngine."""
    connect_args = {}
    if make_url(database_url).get_backend_name() == "postgresql":
        connect_args["server_settings"] = {
            "idle_in_transaction_session_timeout": str(IDLE_IN_TRANSACTION_TIMEOUT_MS)
        }
    return create_async_engine(database_url, echo=echo, future=True, connect_args=connect_args)


def create_sync_engine(
    database_url: str,
    *,
    echo: bool = False,
    pool_size: int | None = None,
    max_overflow: int | None = None,
) -> Engine:
    """Create the synchronous engine required by the current IStorage port.

    `pool_size`/`max_overflow` default to SQLAlchemy's own defaults (5/10)
    when omitted -- only passed explicitly by callers that need a separate,
    isolated connection pool (see container.py's dedicated whale-alerts
    engine) rather than sharing the app's one default-sized pool with
    unrelated workloads (the REST scheduler, reconcile()).
    """
    url = make_url(database_url)
    connect_args = {}
    if url.get_backend_name() == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
        # See IDLE_IN_TRANSACTION_TIMEOUT_MS's own comment (engine.py's
        # async create_engine above) -- same backstop, psycopg's own
        # libpq `options` startup-parameter form instead of asyncpg's
        # `server_settings` dict.
        connect_args["options"] = (
            f"-c idle_in_transaction_session_timeout={IDLE_IN_TRANSACTION_TIMEOUT_MS}"
        )
    elif url.get_backend_name() == "sqlite":
        url = url.set(drivername="sqlite+pysqlite")
    pool_kwargs = {}
    if pool_size is not None:
        pool_kwargs["pool_size"] = pool_size
    if max_overflow is not None:
        pool_kwargs["max_overflow"] = max_overflow
    return sqlalchemy_create_engine(
        url, echo=echo, future=True, connect_args=connect_args, **pool_kwargs
    )
