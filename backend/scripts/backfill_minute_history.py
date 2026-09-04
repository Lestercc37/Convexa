"""One-time backfill of closed 1-minute OHLCV bars for the Indices Pro plan.

Deliberately limited to ~1 month of history (not the plan's full 7 years)
-- confirmed live, 2026-09: a single regular session is 391 bars and
~50-55KB of raw JSON per symbol; even at that count, storing 7 years for
three symbols would run into the low hundreds of MB with indexes, which
this local dev machine (no dedicated server provisioned yet) doesn't need
to carry until there's an actual use for it. Run again later (safe: every
write is an upsert) to extend coverage once that's decided.

Ingestion only -- nothing here reads `minute_bars` back for backtesting or
any other use case yet.

Usage: python -m backend.scripts.backfill_minute_history
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from backend.adapters.providers.thetadata.provider import ThetaDataProvider
from backend.core.container import build_container

logger = logging.getLogger(__name__)

# SPX/VIX/NDX only -- the three symbols the user confirmed carry 7 years
# of history under the active Indices Pro plan. Not extended to the
# equity underlyings (SPY/QQQ/.../DIA) or ES, which aren't covered by
# that plan and were never asked for here.
SYMBOLS = ("SPX", "VIX", "NDX")

# ~1 calendar month back, not 1 trading month -- comfortably covers the
# ~21 trading days the user asked for while staying a simple, obviously-
# correct bound (leap over weekends/holidays is harmless here, unlike
# get_daily_bars' day-count-sensitive callers -- ThetaData just returns
# fewer rows for the days the market was closed).
BACKFILL_DAYS = 30


def backfill() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5.5s %(message)s")
    container = build_container()
    provider = container.market_data_provider
    if not isinstance(provider, ThetaDataProvider):
        raise RuntimeError(
            "backfill_minute_history requires the real ThetaData provider "
            f"(QLL_DATA_PROVIDER=thetadata) -- got {type(provider).__name__}"
        )

    end = date.today()
    start = end - timedelta(days=BACKFILL_DAYS)

    for symbol in SYMBOLS:
        bars = provider.get_minute_bars(symbol, start, end)
        for bar in bars:
            container.storage.save_minute_bar(bar)
        span = f"{bars[0].time.date()} to {bars[-1].time.date()}" if bars else "no bars"
        logger.info("%s: stored %d bars (%s)", symbol, len(bars), span)

    if container.storage_engine is not None:
        with container.storage_engine.connect() as connection:
            from sqlalchemy import text

            row = connection.execute(
                text(
                    "SELECT count(*) AS rows, "
                    "pg_size_pretty(pg_total_relation_size('minute_bars')) AS size "
                    "FROM minute_bars"
                )
            ).mappings().one()
            logger.info("minute_bars total: %d rows, %s on disk", row["rows"], row["size"])


if __name__ == "__main__":
    backfill()
