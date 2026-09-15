"""One-time backfill of DailyGammaReference so Dealer Impact Score has a
real percentile-rank history instead of waiting weeks for natural
accumulation (one row/day at 09:35 ET, via capture_daily_gamma_reference
in the live pipeline).

Confirmed live (2026-09-14) before writing this: ThetaData's history/
greeks/first_order and history/open_interest endpoints both return real
data for a past date -- no need for the trade/quote endpoint. Real,
measured costs that shaped this script's design:
  - history/open_interest supports expiration=* (one call/root/day,
    confirmed live 0.7-8.2s for the 6 index roots).
  - history/greeks/first_order does NOT support expiration=* with a
    specific `date` (confirmed live: "Cannot specify '*' for the date")
    -- one real call per (root, expiration, day), no way around it.
  - That one call's latency does NOT depend mainly on payload size (a
    start_time/end_time window is still used, 09:35:00-09:36:00 ET,
    purely to keep the payload sane -- the same call with no time window
    reached 1.35GB for a single expiration) -- it depends heavily on
    WHICH root, confirmed live across multiple real calls: SPXW averaged
    ~53s/call (3 real samples, 38-70s), NDXP ~10s/call (1 sample),
    VIX+VIXW ~2.75s/call (12-sample average, one full day). SPX/SPXW is
    the dominant, slowest, and least certain cost driver -- not a flat
    per-request constant, contrary to this script's first draft
    assumption (caught by a live smoke test before committing to a full
    run, not assumed).

Index symbols (SPX/NDX/VIX) are backfilled with the SAME all-expirations
methodology get_option_chain uses for net_gamma since PR #132 -- approved
explicitly over the cheaper nearest-only alternative, since mixing the
two methodologies within one percentile-rank history would compare two
incompatible scales (confirmed live in the PR #132 benchmark: ~37x more
contracts for all-expirations vs. nearest-only). This is why those 3
symbols cost far more than the other 12: SPX/NDX/VIX each have dozens of
real unexpired expirations per day across their two roots (confirmed
live, 2026-09-10: SPX+SPXW=60, NDX+NDXP=49, VIX+VIXW=12 -- 121 total),
each needing its own greeks call, vs. exactly 1 for every other symbol.

Real measured cost projection for `--days 60` (all 15 symbols), using the
per-root averages above (SPX/SPXW ~53s, NDX/NDXP ~10s, VIX/VIXW ~2.75s;
equities assumed closer to NDX's end given far fewer near-the-money
strikes than any index, not separately measured per-call):
  - SPX (60 expirations/day x 60 days = 3,600 calls x ~53s) ~= 53.0h
  - NDX (49 x 60 = 2,940 calls x ~10s) ~= 8.2h
  - VIX (12 x 60 = 720 calls x ~2.75s) ~= 0.55h
  - 12 equities/ETFs + ES (780 calls, estimated ~15s avg) ~= 3.25h
  - Combined call-time: ~65h, divided across THETADATA_MAX_CONCURRENT_
    REQUESTS=8 (shared via the same PostgresThetaRequestSlots the live
    Worker uses -- see build_container()) => ~8.1h wall-clock if the
    pool stays saturated. SPX alone is ~80% of total cost and the
    biggest source of uncertainty (38-70s real range on just 3 samples):
    budget 8-14 hours, run overnight or over a weekend, not against a
    market-hours-active Worker if avoidable.

Deliberately separate from the scheduler's own 30s cycle -- this claims
UP TO 8 concurrent request slots itself (ThreadPoolExecutor below), but
never MORE than the existing global limit, because it draws from the
exact same cross-process Postgres-backed semaphore
(PostgresThetaRequestSlots) the live Worker already uses, via the same
build_container() wiring backfill_minute_history.py already established
as the precedent for a one-time script. If the Worker is also busy at
the same time, both sides simply share the same 8 slots and each gets
correspondingly slower -- never a combined 16, which is exactly the kind
of extra concurrent load this week's incidents warned against.

Every write is an upsert (ON CONFLICT (underlying_id, date), see
save_daily_gamma_reference) -- safe to re-run, and (symbol, date) pairs
already present in daily_gamma_reference are skipped before any REST
call is made, so an interrupted run resumes cheaply instead of re-paying
for already-completed days.

Usage: python -m backend.scripts.backfill_daily_gamma_reference [--days 60] [--symbols SPX,NDX]
"""

from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from decimal import Decimal

from backend.adapters.providers.thetadata.provider import ThetaDataProvider
from backend.core.container import Container, build_container
from backend.domain.entities import DailyGammaReference
from backend.domain.underlyings import ACTIVE_UNDERLYINGS_BY_SYMBOL

logger = logging.getLogger(__name__)

DEFAULT_BACKFILL_DAYS = 60
MAX_WORKERS = 8  # matches THETADATA_MAX_CONCURRENT_REQUESTS -- see module docstring


def _trading_day_candidates(end: date, count: int) -> list[date]:
    """Weekdays only, walking backward from `end` (exclusive) -- same
    simple convention backfill_minute_history.py already uses: no market
    holiday calendar here, a genuine holiday just comes back as "no
    data" from ThetaData and is logged/skipped per-day below, same as
    any other real gap."""
    days: list[date] = []
    cursor = end
    while len(days) < count:
        cursor -= timedelta(days=1)
        if cursor.weekday() < 5:
            days.append(cursor)
    return days


def _already_captured(container: Container, symbol: str, days: list[date]) -> set[date]:
    references = container.storage.get_daily_gamma_references(symbol, limit=len(days) + 10)
    return {reference.date for reference in references}


def _backfill_one_day(
    provider: ThetaDataProvider, container: Container, symbol: str, as_of: date
) -> str:
    snapshot = provider.get_historical_gamma_snapshot(symbol, as_of)
    exposures = container.gamma_exposure_calculator.calculate(snapshot.chain)
    aggregate = container.gamma_aggregate_calculator.calculate(exposures, symbol, snapshot.chain.as_of)
    container.storage.save_daily_gamma_reference(
        DailyGammaReference(
            date=as_of,
            symbol=symbol,
            net_gamma=aggregate.net_gamma,
            pc_oi_ratio=snapshot.pc_oi_ratio,
            # Always 0 -- matches get_underlying_snapshot's own
            # documented gap (no 25-delta strikes in the near-the-money
            # range fetched), never computed live either.
            skew_25d=Decimal(0),
            atm_iv=snapshot.atm_iv,
        )
    )
    return f"{symbol} {as_of}: net_gamma={aggregate.net_gamma}, contracts={len(snapshot.chain.contracts)}"


def backfill(days: int, symbols: list[str]) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5.5s %(message)s")
    container = build_container()
    provider = container.market_data_provider
    if not isinstance(provider, ThetaDataProvider):
        raise RuntimeError(
            "backfill_daily_gamma_reference requires the real ThetaData provider "
            f"(QLL_DATA_PROVIDER=thetadata) -- got {type(provider).__name__}"
        )

    candidates = _trading_day_candidates(date.today(), days)
    logger.info(
        "Backfilling %d symbol(s) over %d weekday candidates (%s to %s)",
        len(symbols),
        len(candidates),
        candidates[-1],
        candidates[0],
    )

    tasks: list[tuple[str, date]] = []
    for symbol in symbols:
        if symbol not in ACTIVE_UNDERLYINGS_BY_SYMBOL:
            raise RuntimeError(f"Unknown symbol: {symbol}")
        already = _already_captured(container, symbol, candidates)
        pending = [day for day in candidates if day not in already]
        logger.info(
            "%s: %d already captured, %d pending", symbol, len(already), len(pending)
        )
        tasks.extend((symbol, day) for day in pending)

    if not tasks:
        logger.info("Nothing to do -- every requested (symbol, day) is already captured.")
        return

    succeeded = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_backfill_one_day, provider, container, symbol, day): (symbol, day)
            for symbol, day in tasks
        }
        for future in as_completed(futures):
            symbol, day = futures[future]
            try:
                logger.info(future.result())
                succeeded += 1
            except Exception:
                # One (symbol, day)'s failure (e.g. a real market holiday,
                # a transient ThetaData error) must not abort everything
                # else already in flight or still queued.
                logger.exception("%s %s: backfill failed", symbol, day)
                failed += 1

    logger.info("Done: %d succeeded, %d failed, out of %d pending", succeeded, failed, len(tasks))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=DEFAULT_BACKFILL_DAYS)
    parser.add_argument(
        "--symbols",
        type=str,
        default=",".join(ACTIVE_UNDERLYINGS_BY_SYMBOL),
        help="Comma-separated symbol list (default: all active underlyings)",
    )
    args = parser.parse_args()
    backfill(args.days, [symbol.strip().upper() for symbol in args.symbols.split(",")])


if __name__ == "__main__":
    main()
