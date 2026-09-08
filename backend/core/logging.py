from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from backend.core.settings import Settings


def configure_logging(settings: Settings, log_file: str | None = None) -> None:
    """Configure process-wide logging for the API service.

    `log_file` additionally logs to a rotating file when given (10MB x 3
    backups -- a runtime cap, not "keep forever," since this is meant to
    run indefinitely). stdout stays wired either way, so nothing that
    already relies on it changes. worker.py passes one specifically:
    unlike main.py's uvicorn process (typically run in a visible terminal
    or captured by whatever process manager runs it), the worker's stdout
    commonly isn't watched by anyone -- confirmed live, 2026-09: a
    symbol's underlying-price-stream task dying silently (see
    underlying_price_stream.py's own supervisor) went unnoticed for the
    rest of a trading day for exactly this reason.
    """

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file is not None:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        handlers.append(RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=3))

    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=handlers,
        force=True,
    )
