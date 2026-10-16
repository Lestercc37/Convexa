"""One option-volume correction pass (python -m backend.volume_correction_pass [--force]).

A short-lived process, started by a Windows scheduled task at 10:30, 12:00, 14:00 and 16:05 ET -- never inside the Worker or the stream processor, so it
does not compete with the stream for the GIL. For every option-bearing symbol it asks ThetaData for the day's traded volume per contract of each expiration
in the Tactical window (ONE request per expiration and root, sequential, a pause between them) and stores the result in contract_volume_corrections
(migration 0044), apart from the live counter. Readers of TOTALS (the /chain reply) take max(counter, correction); anything that computes CHANGES of
volume keeps reading the continuous counter. See docs/propuesta-volumen-suscripcion-tardia-2026-10-16.txt.

Guards: refuses to run outside 09:45-16:30 ET on a weekday (the first minutes of the open are the stream's heaviest moment; --force overrides, for tests and reruns), stops
asking at the first failing request (the partial result is still stored: each figure is a real ThetaData volume), and never runs longer than the time box.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from datetime import time as time_of_day

from backend.domain.entities import UnderlyingKind
from backend.domain.underlyings import ACTIVE_UNDERLYINGS_BY_SYMBOL
from backend.domain.use_cases.market_hours import EASTERN_TIME
from backend.domain.use_cases.volume_corrections import expirations_to_correct

logger = logging.getLogger(__name__)

ALLOWED_FROM = time_of_day(9, 45)
ALLOWED_UNTIL = time_of_day(16, 30)
TIME_BOX_SECONDS = 150.0
PAUSE_BETWEEN_REQUESTS_SECONDS = 0.3


def within_allowed_window(now: datetime) -> bool:
    local = now.astimezone(EASTERN_TIME)
    return local.weekday() < 5 and ALLOWED_FROM <= local.time() <= ALLOWED_UNTIL


def run_pass(storage, provider, now: datetime | None = None, sleep=time.sleep, monotonic=time.monotonic) -> dict[str, object]:
    """Asks the provider for each symbol's expirations in the Tactical window and stores what it returns. Returns the summary that main() logs."""
    started = monotonic()
    today = (now or datetime.now(EASTERN_TIME)).astimezone(EASTERN_TIME).date()
    volumes: dict[str, int] = {}
    requests = 0
    stopped_because: str | None = None
    for symbol, underlying in ACTIVE_UNDERLYINGS_BY_SYMBOL.items():
        if underlying.kind == UnderlyingKind.FUTURE or stopped_because:
            continue
        chain = storage.get_latest_chain_snapshot(symbol)
        if chain is None:
            continue
        for expiration in expirations_to_correct([contract.expiration for contract in chain.contracts], today):
            if monotonic() - started > TIME_BOX_SECONDS:
                stopped_because = "time box"
                break
            try:
                volumes.update(provider.get_option_volume_snapshot(symbol, expiration))
            except Exception as error:  # noqa: BLE001 -- the first failing request ends the pass; what is already read is kept
                stopped_because = f"{symbol} {expiration}: {type(error).__name__}"
                logger.warning("Volume correction pass stopped at %s", stopped_because, exc_info=True)
                break
            requests += 1
            sleep(PAUSE_BETWEEN_REQUESTS_SECONDS)
    if volumes:
        storage.save_volume_corrections(volumes)
    return {
        "requests": requests,
        "contracts": len(volumes),
        "volume": sum(volumes.values()),
        "seconds": round(monotonic() - started, 1),
        "stopped_because": stopped_because,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--force", action="store_true", help="run outside the allowed window (tests, reruns)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    now = datetime.now(EASTERN_TIME)
    if not args.force and not within_allowed_window(now):
        logger.info("Volume correction pass skipped: %s ET is outside 09:45-16:30 on a weekday", now.strftime("%a %H:%M"))
        return 0
    from backend.core.container import build_container

    container = build_container()
    try:
        summary = run_pass(container.storage, container.market_data_provider, now)
    finally:
        if container.storage_engine is not None:
            container.storage_engine.dispose()
    logger.info("Volume correction pass done: %s", summary)
    return 1 if summary["stopped_because"] and not summary["contracts"] else 0


if __name__ == "__main__":
    sys.exit(main())
