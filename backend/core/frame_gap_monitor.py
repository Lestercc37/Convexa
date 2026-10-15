"""Arrival-gap monitor for the raw Theta Terminal WebSocket frames. Observation only.

Why it exists: the Terminal logs `SLOW CONSUMER: N packets dropped` in short bursts at the open (2026-10-14
09:36:29-09:37:52, ~205,000 packets; 2026-10-15 09:36:59, 09:47:21-47, 09:48:16-18, ~996,000 packets) while
the Worker's own loop never stalled (PR #272: 0 stalls >= 250 ms, max lag 60-179 ms), its queues stayed at 0
and the frame rate in the burst minutes (53-55k/s) was lower than in minutes with no burst (61k/s). Two
explanations remain and this tells them apart: (a) the Terminal paused for about a second (its JVM, or its REST
work sharing the same process) and dropped packets for the only client it has: then NO frames arrive during the
gap, right before each burst; (b) frames kept arriving and the loss happened somewhere between the Terminal and
the reader: then there is no gap at the burst.

How: the reader callback hands the monotonic arrival time of every frame to `observe()` (two comparisons and a
subtraction per frame). A summary line is logged every `summary_every` seconds with the frame count, the largest
gap, and the few largest gaps of `gap_threshold` or more with their wall-clock time. The Terminal sends a STATUS
frame every second, so a gap of a second or more while the stream is busy means it went silent.

Read-only: it changes nothing about how the frames are read or processed.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime

from backend.domain.use_cases.market_hours import EASTERN_TIME

logger = logging.getLogger(__name__)

DEFAULT_GAP_THRESHOLD_SECONDS = 0.25
DEFAULT_SUMMARY_EVERY_SECONDS = 60.0
# Largest gaps remembered per window: a burst of tiny gaps must not grow memory or the log line.
MAX_GAPS_REMEMBERED = 5


def _clock_time(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, EASTERN_TIME).strftime("%H:%M:%S.%f")[:-3]


class FrameGapMonitor:
    def __init__(
        self,
        *,
        gap_threshold: float = DEFAULT_GAP_THRESHOLD_SECONDS,
        summary_every: float = DEFAULT_SUMMARY_EVERY_SECONDS,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._gap_threshold = gap_threshold
        self._summary_every = summary_every
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._last_at: float | None = None
        self._window_started_at = monotonic_clock()
        self._frames = 0
        self._max_gap = 0.0
        self._gaps_over_threshold = 0
        # (gap seconds, monotonic time at which the gap ended), only the largest few.
        self._largest: list[tuple[float, float]] = []

    def observe(self, now: float) -> None:
        """Hot path: called for every frame with its monotonic arrival time."""
        self._frames += 1
        last = self._last_at
        self._last_at = now
        if last is None:
            return
        gap = now - last
        self._max_gap = max(self._max_gap, gap)
        if gap >= self._gap_threshold:
            self._gaps_over_threshold += 1
            largest = self._largest
            if len(largest) < MAX_GAPS_REMEMBERED:
                largest.append((gap, now))
            else:
                smallest = min(range(len(largest)), key=lambda index: largest[index][0])
                if gap > largest[smallest][0]:
                    largest[smallest] = (gap, now)

    def maybe_report(self, now: float | None = None) -> bool:
        """Logs the window summary once `summary_every` has passed; returns True when it logged."""
        now = self._monotonic_clock() if now is None else now
        elapsed = now - self._window_started_at
        if elapsed < self._summary_every:
            return False
        offset = self._wall_clock() - now
        largest = sorted(self._largest, reverse=True)
        gaps = ", ".join(
            f"{gap * 1000:.0f} ms ending {_clock_time(ended + offset)}" for gap, ended in largest
        )
        logger.info(
            "Frame arrival gaps over the last %.0f s: %d frames (%.0f/s), max gap %.0f ms, %d gaps >= %.0f ms%s",
            elapsed,
            self._frames,
            self._frames / elapsed if elapsed > 0 else 0.0,
            self._max_gap * 1000,
            self._gaps_over_threshold,
            self._gap_threshold * 1000,
            f" (largest: {gaps})" if gaps else "",
        )
        self._window_started_at = now
        self._frames = 0
        self._max_gap = 0.0
        self._gaps_over_threshold = 0
        self._largest = []
        return True
