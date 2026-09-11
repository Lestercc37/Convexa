from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def create_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Create the application AsyncEngine."""
    return create_async_engine(database_url, echo=echo, future=True)


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
    if url.get_backend_name() == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    elif url.get_backend_name() == "sqlite":
        url = url.set(drivername="sqlite+pysqlite")
    pool_kwargs = {}
    if pool_size is not None:
        pool_kwargs["pool_size"] = pool_size
    if max_overflow is not None:
        pool_kwargs["max_overflow"] = max_overflow
    return sqlalchemy_create_engine(url, echo=echo, future=True, **pool_kwargs)
