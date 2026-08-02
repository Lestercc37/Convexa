from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def create_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Create the application AsyncEngine."""
    return create_async_engine(database_url, echo=echo, future=True)


def create_sync_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Create the synchronous engine required by the current IStorage port."""
    url = make_url(database_url)
    if url.get_backend_name() == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    elif url.get_backend_name() == "sqlite":
        url = url.set(drivername="sqlite+pysqlite")
    return sqlalchemy_create_engine(url, echo=echo, future=True)
