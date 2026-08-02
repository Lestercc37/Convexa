from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create the AsyncSession factory used by infrastructure adapters."""
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def create_sync_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create sessions for the synchronous PostgreSQL storage adapter."""
    return sessionmaker(engine, expire_on_commit=False, class_=Session)


async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield one AsyncSession and ensure it is closed after use."""
    async with session_factory() as session:
        yield session
