"""A small in-process cache for read endpoints that every open window polls (desktop app design, docs/diseno-app-escritorio-2026-10-26.txt step 0).

Measured 2026-10-22: GET /market/{symbol} costs 0.2-0.6 s of API CPU for a ~1 KB answer, and every window and every user asks for it on its own
poll. Within a few seconds the answer is the same for all of them, so it is built once per symbol and served to everyone until it expires; requests
that arrive while it is being built wait for that one build (single flight) instead of starting their own. Errors are never cached."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class TtlSingleFlight(Generic[T]):
    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._values: dict[str, tuple[float, T]] = {}
        self._inflight: dict[str, asyncio.Future[T]] = {}

    async def get(self, key: str, build: Callable[[], Awaitable[T]]) -> T:
        now = self._clock()
        cached = self._values.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
        pending = self._inflight.get(key)
        if pending is not None:
            return await asyncio.shield(pending)
        future: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            value = await build()
        except BaseException as error:
            if not future.done():
                future.set_exception(error)
                # Nobody may be waiting on it: mark the exception as retrieved so asyncio does not log it.
                future.exception()
            raise
        else:
            self._values[key] = (self._clock() + self._ttl, value)
            future.set_result(value)
            return value
        finally:
            self._inflight.pop(key, None)
