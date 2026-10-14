"""Event-loop lag and garbage-collector pause monitor.

Why it exists: at the 2026-10-14 open the Theta Terminal logged `SLOW CONSUMER` for three bursts
(09:36:29-31, 09:36:43-44, 09:37:52; about 205,000 packets) while the Worker was the only client on
the stream and the stream ran at 44-51k frames/s. The Worker's read loop is already separate from its
processing loop, and its processing loop logged nothing slow, so the stall is somewhere the existing
logs cannot see: a Python garbage-collection pause, another task holding the loop, or the whole
process not getting CPU. This module tells those apart the next time it happens.

How: a task wakes every `interval` seconds and measures how late it woke up (the loop lag). A lag of
`stall_threshold` seconds or more is logged with the process CPU time used in that span: CPU time close
to the lag means the process itself was busy (a GC pause or a task hogging the loop), CPU time far below
it means the process was not running (starved of CPU or blocked outside Python). Garbage-collection pauses
are timed through `gc.callbacks` and only recorded there (logging from inside a GC callback is not safe);
the task reports them. A one-line summary is logged every `summary_every` seconds.

Read-only: it observes, it changes nothing about how the Worker behaves.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 0.05
DEFAULT_STALL_THRESHOLD_SECONDS = 0.25
DEFAULT_GC_THRESHOLD_SECONDS = 0.05
DEFAULT_SUMMARY_EVERY_SECONDS = 60.0
# A stall can repeat every few milliseconds for a while; the log must not become the next problem.
MAX_STALL_LOGS_PER_MINUTE = 10
GC_PAUSE_BACKLOG = 256


@dataclass(frozen=True, slots=True)
class GcPause:
    generation: int
    seconds: float
    collected: int


@dataclass(frozen=True, slots=True)
class LagSnapshot:
    max_lag_seconds: float
    stalls: int
    gc_pauses: int
    max_gc_pause_seconds: float


class LoopLagMonitor:
    def __init__(
        self,
        name: str,
        *,
        interval: float = DEFAULT_INTERVAL_SECONDS,
        stall_threshold: float = DEFAULT_STALL_THRESHOLD_SECONDS,
        gc_threshold: float = DEFAULT_GC_THRESHOLD_SECONDS,
        summary_every: float = DEFAULT_SUMMARY_EVERY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        cpu_clock: Callable[[], float] = time.process_time,
    ) -> None:
        self._name = name
        self._interval = interval
        self._stall_threshold = stall_threshold
        self._gc_threshold = gc_threshold
        self._summary_every = summary_every
        self._clock = clock
        self._cpu_clock = cpu_clock
        self._task: asyncio.Task[None] | None = None
        self._gc_started_at: float | None = None
        # Appended from the gc callback only; drained by the monitoring task.
        self._gc_pauses: deque[GcPause] = deque(maxlen=GC_PAUSE_BACKLOG)
        self._reset_window()
        self._stall_log_minute = 0
        self._stall_logs_this_minute = 0

    def _reset_window(self) -> None:
        self._window_max_lag = 0.0
        self._window_stalls = 0
        self._window_gc_pauses = 0
        self._window_max_gc_pause = 0.0

    def start(self) -> None:
        if self._task is not None:
            return
        gc.callbacks.append(self._on_gc)
        self._task = asyncio.create_task(self._run(), name=f"loop-lag-monitor-{self._name}")

    async def stop(self) -> None:
        if self._on_gc in gc.callbacks:
            gc.callbacks.remove(self._on_gc)
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def snapshot(self) -> LagSnapshot:
        """The current summary window so far (tests and ad-hoc inspection)."""
        return LagSnapshot(
            self._window_max_lag,
            self._window_stalls,
            self._window_gc_pauses,
            self._window_max_gc_pause,
        )

    def _on_gc(self, phase: str, info: dict[str, int]) -> None:
        # Nothing but timing and a deque append: this runs inside the collector.
        if phase == "start":
            self._gc_started_at = time.perf_counter()
        elif phase == "stop" and self._gc_started_at is not None:
            seconds = time.perf_counter() - self._gc_started_at
            self._gc_started_at = None
            self._gc_pauses.append(
                GcPause(info.get("generation", -1), seconds, info.get("collected", 0))
            )

    def _drain_gc_pauses(self) -> list[GcPause]:
        pauses: list[GcPause] = []
        while self._gc_pauses:
            pauses.append(self._gc_pauses.popleft())
        return pauses

    def _may_log_stall(self, now: float) -> bool:
        minute = int(now // 60)
        if minute != self._stall_log_minute:
            self._stall_log_minute = minute
            self._stall_logs_this_minute = 0
        if self._stall_logs_this_minute >= MAX_STALL_LOGS_PER_MINUTE:
            return False
        self._stall_logs_this_minute += 1
        return True

    async def _run(self) -> None:
        summary_due = self._clock() + self._summary_every
        while True:
            before = self._clock()
            cpu_before = self._cpu_clock()
            await asyncio.sleep(self._interval)
            after = self._clock()
            lag = max(0.0, after - before - self._interval)
            cpu_used = max(0.0, self._cpu_clock() - cpu_before)
            gc_pauses = self._drain_gc_pauses()
            for pause in gc_pauses:
                self._window_gc_pauses += 1
                self._window_max_gc_pause = max(self._window_max_gc_pause, pause.seconds)
            self._window_max_lag = max(self._window_max_lag, lag)
            if lag >= self._stall_threshold:
                self._window_stalls += 1
                if self._may_log_stall(after):
                    long_gc = [p for p in gc_pauses if p.seconds >= self._gc_threshold]
                    logger.warning(
                        "Event loop stalled %.0f ms in %s (process CPU %.0f ms of that span; "
                        "GC pauses >= %.0f ms in it: %s)",
                        lag * 1000,
                        self._name,
                        cpu_used * 1000,
                        self._gc_threshold * 1000,
                        ", ".join(
                            f"gen{p.generation} {p.seconds * 1000:.0f} ms ({p.collected} collected)"
                            for p in long_gc
                        )
                        or "none",
                    )
            if after >= summary_due:
                summary_due = after + self._summary_every
                logger.info(
                    "Loop lag in %s over the last %.0f s: max %.0f ms, %d stalls >= %.0f ms, "
                    "%d GC pauses (max %.0f ms)",
                    self._name,
                    self._summary_every,
                    self._window_max_lag * 1000,
                    self._window_stalls,
                    self._stall_threshold * 1000,
                    self._window_gc_pauses,
                    self._window_max_gc_pause * 1000,
                )
                self._reset_window()
