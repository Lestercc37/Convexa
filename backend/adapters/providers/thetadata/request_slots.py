"""Coordinates ThetaData's real account-wide concurrent-request limit
(THETADATA_MAX_CONCURRENT_REQUESTS, see provider.py) across however many
OS processes end up calling ThetaData -- today just one (everything
still runs in a single process), but built now so Phase 2 of the
process-split (separating the scheduler/whale-alerts-stream/
underlying-price-stream from the API process) doesn't depend on
something unproven. A `threading.Semaphore` only coordinates threads
within one process; two processes each holding their own would let up
to 2x the real limit through.

`theta_request_slots` (see backend/db/migrations/0024_create_theta_
request_slots.py) is pre-seeded with one row per slot. Acquiring is one
atomic UPDATE ... WHERE slot = (SELECT ... FOR UPDATE SKIP LOCKED) --
Postgres's own row-level locking rules out two acquirers ever claiming
the same row, the same guarantee a real semaphore gives, with no race
window. A slot whose acquired_at is older than STALE_AFTER_SECONDS is
treated as free too, recovering one a process crashed while holding
(never ran its `finally`/release) -- the httpx client's own 10s timeout
should make any real call finish or raise well before that, so this
almost never triggers in normal operation; it's the crash backstop, not
the common path.

Deliberately NOT `pg_advisory_lock`: an advisory lock only releases when
its owning connection/transaction closes, so holding one for an entire
ThetaData call (which this measured up to several seconds under
contention) means keeping a pooled Postgres connection tied up for that
whole time -- with up to 8 slots concurrently held, that's up to 8
connections pinned just for this, competing with the pool everything
else needs. Acquire/release here are each a single fast UPDATE that
returns the connection to the pool immediately; the (possibly slow)
ThetaData call itself never holds one.
"""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Iterator, Union

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker

# Kept in sync by hand with THETADATA_MAX_CONCURRENT_REQUESTS in
# provider.py -- that constant's own comment carries the citation for
# the real current value.
STALE_AFTER_SECONDS = 30.0
RETRY_INTERVAL_SECONDS = 0.1
ACQUIRE_TIMEOUT_SECONDS = 15.0

_ACQUIRE_SQL = text(
    """
    UPDATE theta_request_slots
    SET acquired_at = now(), holder = :holder
    WHERE slot = (
        SELECT slot FROM theta_request_slots
        WHERE acquired_at IS NULL OR acquired_at < :stale_before
        ORDER BY slot
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING slot
    """
)
_RELEASE_SQL = text(
    "UPDATE theta_request_slots SET acquired_at = NULL, holder = NULL WHERE slot = :slot"
)


class ThetaSlotsExhaustedError(TimeoutError):
    """No ThetaData request slot became available within the acquire
    timeout -- real, sustained contention worth surfacing, not
    something to wait out silently."""


class InProcessThetaRequestSlots:
    """The pre-existing behavior, unchanged: a plain in-process
    `threading.Semaphore`, correct on its own whenever nothing else in
    a separate OS process could also be calling ThetaData -- which is
    every environment without a real Postgres behind it (tests,
    InMemoryStorage) and, for now, production too, since the process
    split (Phase 2) hasn't happened yet. `PostgresThetaRequestSlots`
    below is the one that actually needs to coordinate across
    processes; this one never does."""

    def __init__(self, limit: int) -> None:
        self._semaphore = threading.Semaphore(limit)

    @contextmanager
    def hold(self) -> Iterator[int]:
        with self._semaphore:
            yield 0


class PostgresThetaRequestSlots:
    """Sync (thread-based) acquirer -- used by ThetaDataProvider's
    existing synchronous REST call path (worker threads today, still
    the only caller)."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        holder: str,
        stale_after_seconds: float = STALE_AFTER_SECONDS,
        retry_interval_seconds: float = RETRY_INTERVAL_SECONDS,
        acquire_timeout_seconds: float = ACQUIRE_TIMEOUT_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._holder = holder
        self._stale_after_seconds = stale_after_seconds
        self._retry_interval_seconds = retry_interval_seconds
        self._acquire_timeout_seconds = acquire_timeout_seconds

    def _try_acquire(self) -> int | None:
        stale_before = datetime.now(timezone.utc) - timedelta(seconds=self._stale_after_seconds)
        with self._session_factory.begin() as session:
            return session.execute(
                _ACQUIRE_SQL, {"holder": self._holder, "stale_before": stale_before}
            ).scalar_one_or_none()

    def acquire(self) -> int:
        deadline = time.monotonic() + self._acquire_timeout_seconds
        while True:
            slot = self._try_acquire()
            if slot is not None:
                return slot
            if time.monotonic() >= deadline:
                raise ThetaSlotsExhaustedError(
                    f"No ThetaData request slot free within {self._acquire_timeout_seconds}s "
                    "-- real, sustained contention across processes."
                )
            time.sleep(self._retry_interval_seconds)

    def release(self, slot: int) -> None:
        with self._session_factory.begin() as session:
            session.execute(_RELEASE_SQL, {"slot": slot})

    @contextmanager
    def hold(self) -> Iterator[int]:
        slot = self.acquire()
        try:
            yield slot
        finally:
            self.release(slot)


class AsyncPostgresThetaRequestSlots:
    """Async (event-loop-based) acquirer -- not wired to any caller yet,
    since nothing in this codebase calls ThetaData from async code
    today (the API process doesn't call ThetaData at all currently).
    Built ahead of Phase 2, where the API is expected to get its own
    ThetaDataProvider back (see the process-split design's point 3/4)
    and would need exactly this, sharing the same theta_request_slots
    table and the same real limit as the sync side."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        holder: str,
        stale_after_seconds: float = STALE_AFTER_SECONDS,
        retry_interval_seconds: float = RETRY_INTERVAL_SECONDS,
        acquire_timeout_seconds: float = ACQUIRE_TIMEOUT_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._holder = holder
        self._stale_after_seconds = stale_after_seconds
        self._retry_interval_seconds = retry_interval_seconds
        self._acquire_timeout_seconds = acquire_timeout_seconds

    async def _try_acquire(self) -> int | None:
        stale_before = datetime.now(timezone.utc) - timedelta(seconds=self._stale_after_seconds)
        async with self._session_factory.begin() as session:
            result = await session.execute(
                _ACQUIRE_SQL, {"holder": self._holder, "stale_before": stale_before}
            )
            return result.scalar_one_or_none()

    async def acquire(self) -> int:
        deadline = time.monotonic() + self._acquire_timeout_seconds
        while True:
            slot = await self._try_acquire()
            if slot is not None:
                return slot
            if time.monotonic() >= deadline:
                raise ThetaSlotsExhaustedError(
                    f"No ThetaData request slot free within {self._acquire_timeout_seconds}s "
                    "-- real, sustained contention across processes."
                )
            await asyncio.sleep(self._retry_interval_seconds)

    async def release(self, slot: int) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(_RELEASE_SQL, {"slot": slot})

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[int]:
        slot = await self.acquire()
        try:
            yield slot
        finally:
            await self.release(slot)


def build_theta_request_slots(
    storage_engine: Engine | None,
    sync_session_factory: sessionmaker[Session] | None,
    holder: str,
    limit: int,
) -> Union[PostgresThetaRequestSlots, InProcessThetaRequestSlots]:
    """`PostgresThetaRequestSlots` when there's a real Postgres behind
    the container (storage_engine is not None), `InProcessThetaRequestSlots`
    otherwise -- same fallback shape container.py already uses for
    `async_market_storage` (SyncStorageAsyncReadAdapter when there's no
    real Postgres to talk async SQL to)."""
    if storage_engine is not None and sync_session_factory is not None:
        return PostgresThetaRequestSlots(sync_session_factory, holder)
    return InProcessThetaRequestSlots(limit)
