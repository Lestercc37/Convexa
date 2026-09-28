"""Phase 1 of the historical-gamma backtest: the real multi-day, multi-symbol
backfill, into the isolated Convexa_backtest database (see
setup_backtest_database.py) -- never the live Convexa database.

Same shape as backfill_daily_gamma_reference.py (day-candidate walk,
ThreadPoolExecutor sharing PostgresThetaRequestSlots so this never draws
more than the account's real THETADATA_MAX_CONCURRENT_REQUESTS=8, no matter
what else -- the live scheduler included -- is also using ThetaData at the
same time), but persists the FULL reconstructed GammaAggregate (gamma_flip,
call_wall, put_wall included, not just net_gamma/pc_oi_ratio/atm_iv) into
gamma_aggregates, since this backtest needs Gamma Flip/Walls positioning
against realized price, not just the percentile-rank inputs Dealer Impact
Score needs.

Idempotent per (symbol, date) via get_gamma_history -- an interrupted run
(including a deliberate early stop before market hours resume, see this
module's own safety note below) resumes cheaply, never re-paying for a day
already captured.

Real measured cost (2026-09-27, live against Convexa's ThetaData
subscription, backtest_dry_run.py's own 7-day AAPL sample): equities average
~11.3s/call, one call/day (single nearest expiration). Indices are far more
expensive -- SPX/NDX/VIX reconstruct EVERY expiration with recorded open
interest that day (get_historical_gamma_snapshot's own documented
methodology, matching get_option_chain's live net_gamma precedent), and
backfill_daily_gamma_reference.py's own docstring already measured SPX at
~53s/call across ~60 expirations/day (its most expensive, least certain
driver). Budget accordingly -- this is why --days defaults conservatively
and the safety note below exists.

SAFETY -- read before running a large --days value close to a trading day:
this shares the exact same account-wide ThetaData request budget the live
scheduler/Worker use the moment the market re-opens. Only ever run a batch
large enough to safely finish (or be safely interrupted -- every write is
an idempotent upsert, so Ctrl+C or a killed process loses nothing except
that one in-flight day) well before the next regular session's 09:30 ET
open. Never leave this running unattended into a live trading day.

ONE PROCESS AT A TIME -- confirmed live, 2026-09-27: running two separate
invocations of this script concurrently (split by symbol, to work around
the interleaving bug this same commit fixes) overwhelmed something local
-- most likely Theta Terminal's own request handling, not the account's
documented 8-concurrent-requests limit itself, since the cross-process
Postgres semaphore (theta_request_slots) was confirmed still fully free
afterward -- and turned nearly the entire run (1,734 of 1,741 equity
tasks, all 228 index tasks) into httpx.ReadTimeout failures within
minutes. A single process honoring the same 8-slot budget internally,
which is what the round-robin fix above exists to make sufficient, has
been reliable in every real run so far; a second concurrent invocation
has not.

Usage: python -m backend.scripts.backfill_historical_gamma [--days 20] [--symbols SPX,NDX,VIX,SPY,QQQ,IWM,DIA,AAPL,NVDA,META]
"""

from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from itertools import zip_longest

from backend.adapters.providers.thetadata.provider import ThetaDataProvider
from backend.adapters.storage.postgresql import PostgreSQLStorage
from backend.core.container import Container, build_container
from backend.domain.underlyings import ACTIVE_UNDERLYINGS_BY_SYMBOL
from backend.scripts._backtest_db import backtest_session_factory

logger = logging.getLogger(__name__)

DEFAULT_BACKFILL_DAYS = 20
MAX_WORKERS = 8  # matches THETADATA_MAX_CONCURRENT_REQUESTS -- see module docstring

# The 10 symbols confirmed with the user, 2026-09-27: indices/ETFs plus the
# 3 most liquid mega-cap names, not the full 16-symbol live roster (AMZN/
# GOOGL/MSFT/TSLA/ES/NQ excluded from THIS backtest -- they keep running
# live in production regardless, and can be added to this list later with
# no script change, same pipeline).
DEFAULT_SYMBOLS = ("SPX", "NDX", "VIX", "SPY", "QQQ", "IWM", "DIA", "AAPL", "NVDA", "META")


def _trading_day_candidates(end: date, count: int) -> list[date]:
    """Same simple convention backfill_daily_gamma_reference.py already
    uses: no market holiday calendar here, a genuine holiday just comes
    back as "no data" from ThetaData and is logged/skipped per-day below."""
    days: list[date] = []
    cursor = end
    while len(days) < count:
        cursor -= timedelta(days=1)
        if cursor.weekday() < 5:
            days.append(cursor)
    return days


def _already_captured(storage: PostgreSQLStorage, symbol: str, days: list[date]) -> set[date]:
    start = datetime.combine(min(days), datetime.min.time(), UTC)
    end = datetime.combine(max(days), datetime.min.time(), UTC) + timedelta(days=1)
    history = storage.get_gamma_history(symbol, start, end, view="structural")
    return {aggregate.as_of.astimezone(UTC).date() for aggregate in history}


def _backfill_one_day(
    provider: ThetaDataProvider,
    container: Container,
    backtest_storage: PostgreSQLStorage,
    symbol: str,
    as_of: date,
) -> str:
    snapshot = provider.get_historical_gamma_snapshot(symbol, as_of)
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
    return (
        f"{symbol} {as_of}: spot={chain.spot_price}, contracts={len(chain.contracts)}, "
        f"gamma_flip={final_aggregate.gamma_flip}, call_wall={final_aggregate.call_wall}, "
        f"put_wall={final_aggregate.put_wall}"
    )


def backfill(days: int, symbols: list[str]) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5.5s %(message)s")
    container = build_container()
    provider = container.market_data_provider
    if not isinstance(provider, ThetaDataProvider):
        raise RuntimeError(
            "backfill_historical_gamma requires the real ThetaData provider "
            f"(QLL_DATA_PROVIDER=thetadata) -- got {type(provider).__name__}"
        )

    backtest_storage = PostgreSQLStorage(backtest_session_factory())
    candidates = _trading_day_candidates(date.today(), days)
    logger.info(
        "Backfilling %d symbol(s) over %d weekday candidates (%s to %s) into Convexa_backtest",
        len(symbols), len(candidates), candidates[-1], candidates[0],
    )

    # Round-robin across symbols, not concatenated one symbol at a time --
    # confirmed live, 2026-09-27: concatenating meant SPX's own ~90 pending
    # days (each needing dozens of sequential per-expiration calls inside
    # ONE task -- get_historical_gamma_snapshot's own near-the-money fetch
    # loop is not itself parallelized) were submitted to the thread pool
    # before a single cheap equity task, so all 8 workers spent hours
    # entirely on SPX while NDX/VIX/every equity sat at zero. Interleaving
    # means a mix of cheap and expensive symbols is always in flight
    # together, so the cheap ones finish early instead of starving behind
    # whichever expensive symbol happened to be listed first.
    pending_by_symbol: dict[str, list[date]] = {}
    for symbol in symbols:
        if symbol not in ACTIVE_UNDERLYINGS_BY_SYMBOL:
            raise RuntimeError(f"Unknown symbol: {symbol}")
        already = _already_captured(backtest_storage, symbol, candidates)
        pending = [day for day in candidates if day not in already]
        logger.info("%s: %d already captured, %d pending", symbol, len(already), len(pending))
        pending_by_symbol[symbol] = pending

    tasks: list[tuple[str, date]] = []
    for round_days in zip_longest(*pending_by_symbol.values()):
        for symbol, day in zip(pending_by_symbol, round_days):
            if day is not None:
                tasks.append((symbol, day))

    if not tasks:
        logger.info("Nothing to do -- every requested (symbol, day) is already captured.")
        return

    succeeded = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_backfill_one_day, provider, container, backtest_storage, symbol, day): (symbol, day)
            for symbol, day in tasks
        }
        for future in as_completed(futures):
            symbol, day = futures[future]
            try:
                logger.info(future.result())
                succeeded += 1
            except Exception:
                # One (symbol, day)'s failure (a real market holiday, a
                # transient ThetaData error) must not abort everything else
                # already in flight or still queued -- same reasoning as
                # backfill_daily_gamma_reference.py's identical guard.
                logger.exception("%s %s: backfill failed", symbol, day)
                failed += 1

    logger.info("Done: %d succeeded, %d failed, out of %d pending", succeeded, failed, len(tasks))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=DEFAULT_BACKFILL_DAYS)
    parser.add_argument(
        "--symbols",
        type=str,
        default=",".join(DEFAULT_SYMBOLS),
        help="Comma-separated symbol list (default: the 10 confirmed for this backtest)",
    )
    args = parser.parse_args()
    backfill(args.days, [symbol.strip().upper() for symbol in args.symbols.split(",")])


if __name__ == "__main__":
    main()
