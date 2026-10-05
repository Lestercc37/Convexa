"""Records a short window of the raw Theta Terminal WebSocket frames to disk.

Why: the 2026-10-05 open showed the stream reader is near its limit, and the
options for more capacity (a different WebSocket library, a native ingestor,
sharding the parsing) can only be compared on REAL traffic, not on estimates.
This captures a minute or two of exactly what the Terminal sends at the open
so those candidates can be benchmarked offline on the same bytes.

Off by default and free when off. Armed by dropping a small JSON file next to
the logs (no restart, no service change):

    logs/stream_capture_request.json
    {"date": "2026-10-06", "start_et": "09:30:00", "seconds": 90}

ThetaStreamHub polls for that file every few seconds. When the window starts
the recorder appends every raw frame it enqueues as one line,

    <arrival epoch milliseconds>\\t<raw frame, one JSON object, no newlines>

to logs/stream_capture_<date>_<start>.tsv, and renames the request to
`.done` when the window ends (or the size cap is hit). Writing is done by a
separate thread in large chunks so the read loop only appends to a list.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

from backend.domain.use_cases.market_hours import EASTERN_TIME

logger = logging.getLogger(__name__)

REQUEST_FILENAME = "stream_capture_request.json"
FLUSH_EVERY_FRAMES = 4000
DEFAULT_SECONDS = 60
MAX_SECONDS = 600
MAX_BYTES = 2 * 1024**3  # hard cap, whatever the request says


class FrameRecorder:
    def __init__(self, path: Path, start_epoch: float, seconds: float) -> None:
        self._path = path
        self._start = start_epoch
        self._end = start_epoch + seconds
        self._buffer: list[str] = []
        self._bytes = 0
        self._frames = 0
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._finished = False
        self._thread = threading.Thread(target=self._write_loop, name="frame-recorder", daemon=True)
        self._thread.start()

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def frames(self) -> int:
        return self._frames

    def record(self, raw: str, now: float | None = None) -> None:
        """Hot path: one comparison per frame outside the window."""
        if self._finished:
            return
        now = time.time() if now is None else now
        if now < self._start:
            return
        if now >= self._end or self._bytes >= MAX_BYTES:
            self.finish()
            return
        line = f"{int(now * 1000)}\t{raw}\n"
        self._buffer.append(line)
        self._bytes += len(line)
        self._frames += 1
        if len(self._buffer) >= FLUSH_EVERY_FRAMES:
            self._flush()

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._flush()
        self._queue.put(None)
        logger.info(
            "Frame recorder: captured %d frames (%.1f MB) to %s", self._frames, self._bytes / 1e6, self._path
        )

    def _flush(self) -> None:
        if self._buffer:
            self._queue.put("".join(self._buffer))
            self._buffer = []

    def _write_loop(self) -> None:
        try:
            with self._path.open("w", encoding="utf-8", newline="") as handle:
                while True:
                    chunk = self._queue.get()
                    if chunk is None:
                        return
                    handle.write(chunk)
        except Exception:
            logger.exception("Frame recorder: writing the capture failed")


def load_request(logs_dir: Path, now: datetime | None = None) -> FrameRecorder | None:
    """Reads logs/stream_capture_request.json and returns an armed recorder, or
    None when there is no (valid, not-yet-over) request. A bad request is
    renamed to `.invalid` so it is not retried every few seconds."""
    request_path = logs_dir / REQUEST_FILENAME
    if not request_path.exists():
        return None
    now = now or datetime.now(EASTERN_TIME)
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        day = datetime.strptime(str(request["date"]), "%Y-%m-%d")  # noqa: DTZ007 -- date only
        hour, minute, second = (int(part) for part in str(request["start_et"]).split(":"))
        start = datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=EASTERN_TIME)
        seconds = min(float(request.get("seconds", DEFAULT_SECONDS)), MAX_SECONDS)
    except (ValueError, KeyError, OSError, TypeError):
        logger.exception("Frame recorder: invalid %s, ignoring it", request_path.name)
        _rename(request_path, ".invalid")
        return None
    if now.timestamp() >= start.timestamp() + seconds:
        _rename(request_path, ".done")  # window already over
        return None
    output = logs_dir / f"stream_capture_{start:%Y%m%d_%H%M%S}.tsv"
    logger.info(
        "Frame recorder armed: %s for %.0fs starting %s ET -> %s", request_path.name, seconds, start, output.name
    )
    return FrameRecorder(output, start.timestamp(), seconds)


def mark_done(logs_dir: Path) -> None:
    _rename(logs_dir / REQUEST_FILENAME, ".done")


def _rename(path: Path, suffix: str) -> None:
    try:
        path.replace(path.with_name(path.name + suffix))
    except OSError:
        logger.exception("Frame recorder: could not rename %s", path)
