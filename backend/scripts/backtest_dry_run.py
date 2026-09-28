"""Small feasibility test for the historical-gamma backtest -- Phase 0's
companion, run once against a handful of real trading days before committing
to the full multi-year, multi-symbol pull. Answers three questions with real
data, not assumptions:

  1. Does ThetaData's historical greeks/open-interest endpoints really return
     usable data for past AAPL sessions (confirmed already, 2026-09-14, for
     backfill_daily_gamma_reference.py's own symbols -- this is the first
     real measurement for an equity specifically, not just assumed "closer
     to NDX's end" per that script's own docstring)?
  2. How long does one (symbol, day) reconstruction actually take for an
     equity -- the number every later cost estimate for the full backfill
     depends on?
  3. Does the whole path -- fetch -> calculate (exposures, aggregate, gamma
     flip, walls) -> persist -- work end to end against the isolated
     Convexa_backtest database (see setup_backtest_database.py), without
     touching the live Convexa database at all?

Deliberately simpler than the live CalculateGammaExposureOrchestrator's own
_build_view: no wide/narrow dual-width split for Gamma Flip vs. Walls, no
0DTE wall exclusion. get_historical_gamma_snapshot already returns a single
near-the-money-width chain (see that method's own docstring), which doesn't
map cleanly onto _build_view's window-days-based expiration filtering in the
first place -- the two are inherently different shapes, not a corner cut for
convenience. The real Phase-1 backfill script needs to decide, deliberately,
whether reproducing _build_view's exact structural-view nuance is worth
extracting into a shared function; this script only needs plausible numbers
to sanity-check the pipeline, not production-faithful ones.

Real measured result (2026-09-27, 7 real AAPL trading days, single-threaded):
min=8.5s avg=11.3s max=16.8s per (symbol, day) -- confirms and refines
backfill_daily_gamma_reference.py's own "equities assumed closer to NDX's
end (~10s), not separately measured per-call" placeholder. All 7 days
succeeded with plausible numbers (Gamma Flip and both Walls within a few
dollars of that day's real spot).

Usage: python -m backend.scripts.backtest_dry_run [--symbol AAPL] [--days 7]
"""

from __future__ import annotations

import argparse
import logging
import time
from dataclasses import replace
from datetime import date, timedelta

from backend.adapters.providers.thetadata.provider import ThetaDataProvider
from backend.adapters.storage.postgresql import PostgreSQLStorage
from backend.core.container import build_container
from backend.scripts._backtest_db import backtest_session_factory

logger = logging.getLogger(__name__)

DEFAULT_SYMBOL = "AAPL"
DEFAULT_DAYS = 7


def _recent_weekdays(count: int) -> list[date]:
    """Same simple convention backfill_daily_gamma_reference.py already
    uses: no market holiday calendar here, a genuine holiday just comes
    back as "no data" from ThetaData and is logged/skipped, same as any
    other real gap -- not worth a dependency for a one-off feasibility
    check."""
    days: list[date] = []
    cursor = date.today()
    while len(days) < count:
        cursor -= timedelta(days=1)
        if cursor.weekday() < 5:
            days.append(cursor)
    return days


def run(symbol: str, days: int) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5.5s %(message)s")
    container = build_container()
    provider = container.market_data_provider
    if not isinstance(provider, ThetaDataProvider):
        raise RuntimeError(
            "backtest_dry_run requires the real ThetaData provider "
            f"(QLL_DATA_PROVIDER=thetadata) -- got {type(provider).__name__}"
        )

    backtest_storage = PostgreSQLStorage(backtest_session_factory())
    candidates = _recent_weekdays(days)
    logger.info("Dry run: %s over %d weekday candidates (%s to %s)",
                symbol, len(candidates), candidates[-1], candidates[0])

    succeeded = 0
    failed = 0
    call_seconds: list[float] = []
    for as_of in candidates:
        started_at = time.perf_counter()
        try:
            snapshot = provider.get_historical_gamma_snapshot(symbol, as_of)
            elapsed = time.perf_counter() - started_at
            call_seconds.append(elapsed)

            chain = snapshot.chain
            exposures = container.gamma_exposure_calculator.calculate(chain)
            aggregate = container.gamma_aggregate_calculator.calculate(exposures, symbol, chain.as_of)
            gamma_flip = container.gamma_flip_calculator.calculate(aggregate, chain.spot_price)
            walls = container.wall_calculator.calculate(aggregate)
            final_aggregate = replace(
                aggregate,
                gamma_flip=gamma_flip.gamma_flip_price,
                call_wall=walls.call_wall.strike if walls.call_wall else None,
                put_wall=walls.put_wall.strike if walls.put_wall else None,
            )
            backtest_storage.save_gamma_aggregate(final_aggregate)

            logger.info(
                "%s %s: %.1fs, spot=%s, contracts=%d, net_gamma=%s, gamma_flip=%s, "
                "call_wall=%s, put_wall=%s",
                symbol, as_of, elapsed, chain.spot_price, len(chain.contracts),
                aggregate.net_gamma, final_aggregate.gamma_flip,
                final_aggregate.call_wall, final_aggregate.put_wall,
            )
            succeeded += 1
        except Exception:
            logger.exception("%s %s: dry run failed", symbol, as_of)
            failed += 1

    if call_seconds:
        logger.info(
            "Timing over %d successful calls: min=%.1fs avg=%.1fs max=%.1fs",
            len(call_seconds), min(call_seconds),
            sum(call_seconds) / len(call_seconds), max(call_seconds),
        )
    logger.info("Done: %d succeeded, %d failed, out of %d candidates", succeeded, failed, len(candidates))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", type=str, default=DEFAULT_SYMBOL)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    args = parser.parse_args()
    run(args.symbol.strip().upper(), args.days)


if __name__ == "__main__":
    main()
