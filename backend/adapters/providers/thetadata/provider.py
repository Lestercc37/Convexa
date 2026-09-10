from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, TypeVar

import httpx
import websockets

from backend.adapters.providers.thetadata.request_slots import (
    InProcessThetaRequestSlots,
    PostgresThetaRequestSlots,
)
from backend.domain.entities import (
    ContractType,
    DailyBar,
    FlowEvent,
    FlowEventType,
    MarketSnapshot,
    MinuteBar,
    OptionChain,
    OptionContract,
    OptionGreeks,
    QuoteEvent,
    Side,
    UnderlyingKind,
    UnderlyingTradeEvent,
    utc_now,
)
from backend.domain.underlyings import ACTIVE_UNDERLYINGS_BY_SYMBOL
from backend.domain.use_cases.calculate_bsm_greeks import calculate_bsm_greeks
from backend.domain.use_cases.calculate_near_the_money_width import calculate_near_the_money_width
from backend.domain.use_cases.market_hours import EASTERN_TIME, MARKET_CLOSE_ET, is_market_open

logger = logging.getLogger(__name__)

# Confirmed with the user (2026-09-01 investigation, docs/use-cases.md):
# ThetaData's per-call quota cost doesn't depend on how many strikes a
# response returns, and the documented Options Standard concurrent-
# subscription caps (10,000 quote / 15,000 trade) leave enormous
# headroom even at ATR x 1.5-derived widths for all 11 active symbols —
# so this over-fetches generously (201 strikes: 100 below + ATM + 100
# above) and filters client-side to spot +/- the width from
# calculate_near_the_money_width, instead of asking ThetaData for
# exactly the strikes needed (its `strike_range` parameter is a strike
# COUNT, not a price distance, so there's no way to ask for "just the
# strikes within $X of spot" server-side). 100 is generous margin above
# every width in the investigation's table, including a stress-scenario
# ATR — see calculate_near_the_money_width.py for the width itself.
NEAR_THE_MONEY_OVERFETCH_STRIKE_RANGE = 100
# Guaranteed minimum entries kept even if ATR x 1.5 filtering would
# otherwise produce zero matches (e.g. an unusually calm 14-day window
# giving a width narrower than this symbol's own strike spacing) — the
# closest strikes to spot, same order of magnitude as the old fixed
# n=1 baseline (3 strikes x 2 contract types), never nothing.
MINIMUM_NEAR_THE_MONEY_ENTRIES = 6

# Data sources per field, confirmed with real ThetaData responses before
# this adapter was written (see docs/use-cases.md):
#
# - bid/ask/delta/theta/vega/implied_vol/underlying_price come from a
#   single GET /v3/option/snapshot/greeks/first_order call (Options
#   Standard tier).
# - open_interest: GET /v3/option/snapshot/open_interest.
# - gamma/vanna/charm: NOT available at Options Standard (the second-
#   order greeks endpoint requires Professional) — calculated instead
#   via Black-Scholes-Merton (`calculate_bsm_greeks`), using the
#   first_order response's own underlying_price/implied_vol plus SOFR
#   (GET /v3/interest_rate/history/eod, FREE tier) as the risk-free
#   rate. Time to expiration: real elapsed seconds to 4:00pm ET / 86400
#   / 365 — confirmed against ThetaData's own reported delta on real
#   0DTE and multi-week contracts, at two points in the trading day
#   (whole-calendar-day counts collapse to zero for 0DTE and don't
#   reconcile).
# - `last` (last trade price): ThetaData's snapshot has no such field —
#   uses the bid/ask midpoint as a documented approximation (the same
#   fallback reached, independently, during the FlashAlpha investigation
#   earlier in this project — FlashAlphaDataProvider itself was never
#   built; ThetaData was chosen instead).
# - occ_symbol: not returned by ThetaData either — built the same way
#   MockDataProvider._contract() already does.
# - volume (cumulative, for WhaleAlertsEngine's delta-based detection):
#   ThetaData's REST trade snapshot only reports the size of the single
#   most recent trade, not a running total — accumulated instead from
#   the Trade Stream (WebSocket), summing every message's `size` since
#   this provider started. Validated against real market-open data
#   (2026-08-31, 9:32-10:22 AM ET): 100.6-100.8% coverage against the
#   same window's REST OHLC volume/count, zero disconnections in 50
#   minutes spanning the historically riskiest window for this vendor.
# - MarketSnapshot.volume (the underlying's own session-cumulative share
#   volume, used by Anchored VWAP): originally blocked on the Stocks/
#   Indices subscriptions not being active, the same limitation the
#   FlashAlpha investigation ran into with its own equivalent gap. That
#   subscription turned out to be unnecessary — GET /v3/stock/snapshot/
#   ohlc (or /v3/index/snapshot/ohlc for indices, whose own volume is
#   always 0 — an index has no share volume of its own) is a plain REST
#   snapshot call that already returns this, confirmed live (2026-09
#   investigation, SPY: 16,396,508) — see _fetch_underlying_volume.
#   `underlying_price` itself was never affected by this gap either way
#   — it comes from the options endpoint above.
# - get_daily_bars: GET /v3/stock/history/eod (equities) or
#   /v3/index/history/eod (indices) — both confirmed working against
#   real data without needing the Stocks/Indices live-quote
#   subscription. No equivalent endpoint was found for futures (ES) —
#   returns an empty list for that underlying kind, documented the same
#   way as the other known gaps above.
# - atm_iv/pc_oi_ratio on MarketSnapshot: approximated from the same
#   near-the-money contracts already fetched for the chain (mean
#   implied_vol; put/call open interest ratio) — genuinely an
#   approximation, not the true market-wide ATM IV or 25-delta skew a
#   full chain would give. skew_25d stays `0`, documented: computing it
#   for real would need 25-delta strikes specifically, outside the
#   near-the-money range already confirmed for this adapter's scope.

RATE_SYMBOL = "SOFR"
RECONCILE_INTERVAL_SECONDS = 20 * 60
STATUS_STALE_AFTER_SECONDS = 15
RECONNECT_BASE_DELAY_SECONDS = 2
RECONNECT_MAX_DELAY_SECONDS = 60

# Confirmed live, 2026-09, real market open, from the Worker's own log:
# the exponential backoff above never actually resets in practice, for
# any of the 3 stream classes -- `_run()`'s `delay = RECONNECT_BASE_
# DELAY_SECONDS` right after `await self._connect_and_consume()` is
# unreachable dead code, because _connect_and_consume() (via _consume()'s
# own `while True`) never returns normally; it only ever exits by
# raising. So once *anything* pushes delay up, it climbs monotonically
# for the rest of the process's life and gets stuck at
# RECONNECT_MAX_DELAY_SECONDS (60s) -- observed live: 2s -> 4s -> 8s ->
# 16s -> 32s -> 60s, then every single subsequent reconnect (natural
# near-the-money widening, or the data-silence watchdog above forcing
# one) waited a full 60s before even attempting, regardless of how the
# previous connection actually behaved. The watchdog made this actively
# worse, not better: every reconnect it forces also ratchets a backoff
# that never comes back down.
#
# STABLE_CONNECTION_RESET_SECONDS=30, not an arbitrary number: 2x
# STATUS_STALE_AFTER_SECONDS (15s) -- a connection that outlives twice
# the heartbeat-staleness window has clearly demonstrated it can
# actually receive traffic reliably, so a fresh failure after that point
# is a new problem, not a continuation of whatever caused the last one.
# This also comfortably covers a watchdog-forced reconnect in the common
# case: DATA_SILENCE_THRESHOLD_SECONDS (20s) is itself already how long
# the connection was alive and receiving *something* before the watchdog
# even noticed data had stopped, so a watchdog-triggered reconnect
# reaching this threshold is the expected case, not a fluke -- it's
# exactly the "the connection was fine, something else glitched" case
# this reset exists for. A connection that can't even stay up for half a
# minute, repeatedly, still escalates without resetting -- that's a
# genuinely different, ongoing problem this must keep protecting
# against, not paper over.
STABLE_CONNECTION_RESET_SECONDS = 2 * STATUS_STALE_AFTER_SECONDS

# Real-DATA silence watchdog (ThetaUnderlyingTradeStream, ThetaQuoteStream)
# -- confirmed live, 2026-09, with a controlled instrumented reproduction
# (forced a Trade/Quote Stream reconnect, then measured message arrival
# with time.perf_counter() on all 3 streams): Theta Terminal can stop
# delivering TRADE-type messages to an already-open connection after a
# *different* connection from this same client reconnects and re-sends
# its own subscribe burst -- while that affected connection's own STATUS
# heartbeat (the existing STATUS_STALE_AFTER_SECONDS check above) keeps
# arriving completely normally, once a second, indefinitely. The existing
# heartbeat is therefore blind to this failure mode by construction: it
# only asks "is *any* message arriving", never "is real data arriving".
# Reproduced 4/4 times, ~2s after the trigger every time (matching
# RECONNECT_BASE_DELAY_SECONDS, i.e. exactly when the *other* stream's
# reconnect attempt actually lands). Manually forcing the affected
# stream's own reconnect (the same request_reconnect() mechanism this
# watchdog now automates) restored delivery within one reconnect cycle
# every time, sustained for the rest of that observation window --
# that's the empirical basis for reusing request_reconnect() here rather
# than inventing a different recovery mechanism.
#
# 20s, not the 15s STATUS threshold: deliberately looser, since this is a
# softer signal than "no message of any kind" -- ThetaUnderlyingTradeStream
# alone registers every active non-future underlying at once (14 highly
# liquid symbols) *plus* whatever cross-contaminated option trades Theta
# Terminal broadcasts to it (confirmed live: thousands/second during
# active windows), so genuine, healthy silence across all of that for
# anywhere near 20s during real market hours essentially never happens --
# but a single quiet contract, or the first few seconds right after a
# fresh connection, still shouldn't trigger a reconnect on its own. 20s
# also stays comfortably under the 30s REST-scheduler poll's own staleness
# ceiling, so self-healing here is never slower than just waiting for that
# fallback would have been anyway. Gated on is_market_open() so a
# genuinely quiet closed market never trips it.
DATA_SILENCE_THRESHOLD_SECONDS = 20
DATA_SILENCE_CHECK_INTERVAL_SECONDS = 5

# Investigation instrumentation (2026-09-09): after PR #122 consolidated
# 3 separate connections into ThetaStreamHub's one shared read loop, the
# chart and Whale Alerts froze together mid-session (backend.worker's own
# process disappeared from its log with no traceback, no graceful
# shutdown message -- see the incident's own PR for the raw evidence).
# One live hypothesis was that internal fan-out to the 3 logical
# consumers (Whale Alerts, Quote, underlying price) uses a *blocking*
# queue write inside the shared read loop, so one slow consumer could
# stall message delivery for the other two -- direct code inspection
# ahead of adding this instrumentation already found that hypothesis, as
# literally stated, false: every fan-out uses `put_nowait()` on an
# *unbounded* `asyncio.Queue` (no `maxsize`), which is synchronous and
# cannot block regardless of how far behind a consumer falls. What direct
# inspection did find, in the same shared loop, is a real candidate:
# `_reconcile()` (see its own docstring) makes one synchronous REST call
# per registered contract in a plain `for` loop, and the loop awaits it
# via `asyncio.to_thread` -- since `_consume()` is single-threaded, it
# cannot call `websocket.recv()` again until that whole sequential REST
# pass finishes, for however long that ends up taking. This existed
# before the refactor too (isolated then to just the option-trade
# connection); multiplexing all 3 logical streams onto it means a slow
# reconcile now delays QUOTE and underlying TRADE messages too, not just
# option TRADE. These constants back active logging placed at every
# dispatch and around `_reconcile()` itself, deployed and observed live
# against real market data before drawing any conclusion -- not proven
# yet as *the* cause of the specific process death (that failure mode,
# no exception ever logged, is more severe than what a single stalled
# read loop alone would produce -- that would raise ConnectionError, get
# caught by _run(), and reconnect, not kill the whole process silently),
# but a confirmed, real blocking point in the same loop worth measuring
# regardless.
DISPATCH_SLOW_THRESHOLD_SECONDS = 0.1
LOOP_ITERATION_SLOW_THRESHOLD_SECONDS = 0.1
# Half of STATUS_STALE_AFTER_SECONDS -- not an arbitrary number: if
# _reconcile() alone eats more than half the heartbeat-staleness budget,
# it has materially eaten into the margin before Theta Terminal's own
# STATUS messages would be judged stale and the connection torn down for
# an unrelated reason, even if this one call doesn't blow the budget by
# itself.
RECONCILE_DANGEROUS_THRESHOLD_SECONDS = STATUS_STALE_AFTER_SECONDS / 2
QUEUE_DEPTH_LOG_INTERVAL_SECONDS = 60

# Fix (2026-09-10), following the investigation instrumentation above:
# confirmed live overnight, 2026-09-09 into 2026-09-10 (~16 hours, ~48
# cycles) that reconcile() reliably blocks _consume()'s own read loop for
# 44-55s every single RECONCILE_INTERVAL_SECONDS cycle (676 contracts),
# 300-370% over STATUS_STALE_AFTER_SECONDS every time -- not a rare edge
# case, a guaranteed one. reconcile() now runs on its own independent
# task (_run_reconcile_loop) instead of inline in _consume()'s loop, so
# it can never again delay websocket.recv() for any of the 3 logical
# streams. The queues below already used put_nowait() (confirmed
# non-blocking, see DISPATCH_SLOW_THRESHOLD_SECONDS' own comment) but
# were unbounded -- fine while nothing could ever fall behind, but with
# reconcile() no longer the guaranteed periodic stall, an unbounded queue
# stops being a safety net and starts being a way for a genuinely stuck
# consumer to grow memory forever without anyone noticing. Bounded queues
# make that failure mode loud (WHALE_ALERTS/QUOTE) or simply irrelevant
# (UNDERLYING, where only the latest price ever matters for a 0DTE
# chart) instead of silent.
#
# Sized from a real trade-volume data point (16,407 trade messages over
# the 50-minute 2026-08-31 SPX market-open window, historically the
# riskiest window for this vendor -- see docs/use-cases.md for the same
# 50-minute window's own coverage validation, though that entry doesn't
# itself state a raw message count or name SPX specifically; the 16,407
# figure is taken at face value, not independently re-derived in this
# repo). That's ~5.5 messages/sec sustained for the single highest-volume
# symbol. TRADE_QUEUE_MAXSIZE
# gives roughly 15 minutes of buffering at 2x that peak rate before a
# stuck consumer would ever hit the wall -- generous, not unlimited.
# QUOTE_QUEUE_MAXSIZE is larger: quote updates are well known to run at
# a higher rate than executed trades in options microstructure, and no
# equivalent measured quote-rate data point exists yet, so this errs
# larger rather than guessing a tight number from nothing.
TRADE_QUEUE_MAXSIZE = 5_000
QUOTE_QUEUE_MAXSIZE = 10_000
# Small and bounded, not "generous" -- a stale price tick has no
# operational value for a 0DTE chart once a newer one exists, so this
# queue drops the OLDEST entry on overflow (see _dispatch_dropping)
# instead of alerting; 10 is already generous slack for one symbol's
# consumer to be a few ticks behind under completely normal conditions.
UNDERLYING_QUEUE_MAXSIZE = 10

# ThetaData's real concurrency limit is per ACCOUNT, not per endpoint or
# symbol, and doesn't add up across subscriptions — the highest tier
# among them governs. Was 4 (Options/Stock Standard, the account's
# highest tier at the time). Confirmed live, 2026-09, straight from the
# running Theta Terminal's own log (C:\tmp\terminal-latest.log) after
# today's Indices Pro upgrade -- not the docs, not a guess:
#   Subscriptions: Stock: STANDARD Options: STANDARD Index: PROFESSIONAL
#   Max concurrent requests: 8
# Index is now the account's highest tier, so 8 governs. If a future
# plan change lowers this again, re-check that log line rather than
# assuming -- ThetaData's public docs table (docs.thetadata.us,
# Subscriptions page) agrees PRO = 8 per data category, but the
# account-wide max the terminal actually enforces is the one number
# that matters here.
#
# Nothing enforced this before this constant existed — the near-zero
# real concurrency observed at the time was an accidental side effect
# of the scheduler's sequential per-symbol loop (backend/core/
# scheduler.py), not a designed safeguard, so this stays preventive:
# if that loop is ever parallelized further for performance, this still
# holds the real limit.
#
# Enforced today via request_slots.py, not a bare `threading.Semaphore`
# — see that module's own docstring. In-process only for now
# (InProcessThetaRequestSlots, still just a threading.Semaphore under
# the hood) since everything still runs in one process; the
# Postgres-backed PostgresThetaRequestSlots exists ahead of the process
# split (Phase 2) that will actually need cross-process coordination of
# this same limit, and container.py already wires it in whenever a real
# Postgres is configured.
THETADATA_MAX_CONCURRENT_REQUESTS = 8

# Short-lived, in-process cache for the near-the-money chain, keyed by
# (symbol, expiration) — get_option_chain() and get_underlying_snapshot()
# both request the exact same near-the-money data for a symbol when
# called back-to-back for the same refresh cycle (confirmed: both call
# _fetch_near_the_money(symbol, expiration=None)), so the second call
# reuses the first's result instead of re-fetching it. Deliberately NOT
# hoisted into the provider-agnostic RefreshUnderlyingSnapshotUseCase —
# MockDataProvider's get_underlying_snapshot() returns independent,
# hand-picked fixture values (not derived from its chain at all), so
# skipping that provider call there would silently change Mock's
# behavior. Kept well under the scheduler's cycle interval so it never
# risks spanning across cycles, comfortably above the real elapsed time
# between these two calls in practice. Deliberately short — unlike open
# interest below, bid/ask/IV genuinely change from one poll to the next
# (confirmed live, 2026-09 investigation: every contract's bid/ask/IV
# changed across a ~70s window), so this cache must not outlive a single
# refresh cycle's own back-to-back calls.
NEAR_THE_MONEY_CACHE_TTL_SECONDS = 10.0

# Open interest, unlike the near-the-money chain above, is confirmed
# static intraday — Open Interest for US options is calculated and
# published by the OCC once per trading day, not continuously (a market-
# structure fact, not a ThetaData quirk); confirmed live too (2026-09
# investigation): 0 of 64 SPX contracts' open_interest changed across a
# ~70s window where every one of those same contracts' bid/ask/IV did
# change, ruling out a stale/closed-market feed as the explanation.
# Cached far longer than the near-the-money chain as a result — 20
# minutes is still conservative relative to "changes once a day," not a
# tight bound chosen to just barely avoid staleness.
OPEN_INTEREST_CACHE_TTL_SECONDS = 20 * 60.0

# Daily bars close once a day (an EOD bar can't change again until the
# next session closes), yet get_daily_bars() was being re-fetched from
# ThetaData on every single scheduler cycle for all 15 active symbols --
# confirmed live, 2026-09: this was the one call in
# RefreshUnderlyingSnapshotUseCase's 3-call-per-symbol sequence with no
# cache at all, unlike near-the-money/open interest above, and a real
# contributor to the threadpool contention that made /gamma and /market
# queue behind the scheduler (see the /gamma/{symbol} route's own
# docstring). Same 20-minute TTL as open interest, same reasoning:
# conservative relative to "changes once a day," not a tight bound.
DAILY_BARS_CACHE_TTL_SECONDS = 20 * 60.0

# ThetaData splits certain broad-based, cash-settled index options into
# two independently-quoted root symbols: the legacy AM-settled root
# (e.g. "SPX") and the PM-settled weekly/0DTE root (e.g. "SPXW") — both
# list real, distinct, separately-traded contracts, confirmed live
# (2026-09 investigation): even on a shared/overlapping expiration
# (e.g. both roots list 2026-09-18), the same strike/right's bid/ask
# and quote timestamp genuinely differ between the two roots — they are
# not aliases of one underlying order book, so combining them must
# never deduplicate by (strike, expiration, right) alone, only add both
# roots' contracts side by side. Before this, `_fetch_near_the_money`
# only ever queried the bare root, so it could never see anything
# nearer than the next AM-settled monthly (confirmed: SPX's own root
# lists only 2026-09-18, 10-16, 11-20...; SPXW lists 2026-09-03, 09-04,
# 09-08... same day). Confirmed this is genuinely a broad-index-option
# thing, not something every symbol needs: SPY/QQQ/IWM/DIA (equity/ETF
# options) already return same-day expirations under their own single
# root, and "SPYW"/"QQQW"/"DIAW" aren't valid ThetaData roots at all.
# VIX shows the same split (a real "VIXW" root exists, with nearer
# expirations than VIX's own root) -- unlike SPX/SPXW and NDX/NDXP,
# confirmed live (2026-09) that VIX's and VIXW's near-term expirations
# never actually overlap: VIX only lists the standard monthlies (e.g.
# 2026-09-16, 10-21, 11-18 -- 3rd Wednesday), and VIXW's own listing
# skips those same dates and only carries the weeklies around them (e.g.
# 2026-09-02, 09-09, 09-23, 09-30). So the "must not dedupe, only add
# both roots' contracts side by side" reasoning above still holds as a
# defensive default, but for VIX specifically there is currently no
# shared expiration where it would ever actually matter.
_WEEKLY_ROOT_BY_SYMBOL: dict[str, str] = {
    "SPX": "SPXW",
    "NDX": "NDXP",
    "VIX": "VIXW",
}


def _roots_for_symbol(symbol: str) -> tuple[str, ...]:
    weekly_root = _WEEKLY_ROOT_BY_SYMBOL.get(symbol)
    return (symbol, weekly_root) if weekly_root else (symbol,)


def _build_occ_symbol(
    root: str, expiration: date, contract_type: ContractType, strike: Decimal
) -> str:
    suffix = "C" if contract_type == ContractType.CALL else "P"
    return f"{root}{expiration:%y%m%d}{suffix}{int(strike * 1000):08d}"


def _parse_et_timestamp(raw: str) -> datetime:
    """ThetaData timestamps are naive strings in US Eastern Time, not UTC
    (confirmed by comparing a live quote's timestamp against the real
    system clock during real market hours) — never call `.astimezone()`
    on the naive result without attaching this tzinfo first."""
    return datetime.fromisoformat(raw).replace(tzinfo=EASTERN_TIME)


def _time_to_expiration_years(expiration_date: date, now_et: datetime) -> Decimal:
    """Real elapsed seconds to 4:00pm ET / 86400 / 365 (ACT/365).

    Confirmed against ThetaData's own reported delta on real contracts
    (0DTE and ~3 weeks out, at two points in the trading day) — whole
    calendar-day counts collapse to zero for 0DTE and don't reconcile;
    this convention matched to within 0.0002 total absolute delta error
    across 7 real strikes.
    """
    close = datetime.combine(expiration_date, MARKET_CLOSE_ET, tzinfo=EASTERN_TIME)
    seconds_remaining = (close - now_et).total_seconds()
    if seconds_remaining <= 0:
        return Decimal(0)
    return Decimal(seconds_remaining) / Decimal(86400) / Decimal(365)


def _nearest_expiration_cutoff(now_et: datetime) -> date:
    """The earliest expiration date still eligible to be picked as the
    "nearest" one -- before MARKET_CLOSE_ET today's own date counts (a
    genuine 0DTE must not be wrongly excluded); at/after MARKET_CLOSE_ET
    today is excluded too, same cutoff the scheduler's own is_market_open
    gate uses. Confirmed live, 2026-09: past 16:00 ET today's 0DTE
    already has time_to_expiration<=0 in calculate_bsm_greeks, which
    zeroes gamma on every contract, so it can no longer stand in as
    "nearest" once the market's actually closed for the day.
    """
    today = now_et.date()
    return today if now_et.time() < MARKET_CLOSE_ET else today + timedelta(days=1)


def _log_req_response(stream_name: str, message: dict[str, Any]) -> None:
    """Logs ThetaData's per-subscription acknowledgment — confirmed live
    (2026-09 investigation against the real Theta Terminal, Stocks/Index
    plans active) that a `{"header": {"type": "REQ_RESPONSE", ...,
    "response": "SUBSCRIBED", "req_id": N}}` message arrives immediately
    after every subscribe request. Previously fell through the unhandled-
    message-type case in all three stream classes below (only "STATUS"
    and "TRADE"/"QUOTE" were branched on) — a rejected subscription
    (`response` anything other than "SUBSCRIBED") would have looked
    identical to "no data yet", with no way to tell the two apart.

    Deliberately logs rather than raising: one contract/symbol among
    many being rejected shouldn't tear down a connection that's still
    correctly serving everything else it subscribed to. A real rejection
    has never been observed — forcing one would mean risking the shared,
    already-running Theta Terminal the live backend depends on — so this
    is the logging half of the fix, verified with simulated messages
    only (see tests/test_thetadata_provider.py).
    """
    header = message.get("header", {})
    response = header.get("response")
    req_id = header.get("req_id")
    if response == "SUBSCRIBED":
        logger.debug("%s subscription confirmed (req_id=%s)", stream_name, req_id)
    else:
        logger.error(
            "%s subscription NOT confirmed (req_id=%s): response=%r, message=%s",
            stream_name,
            req_id,
            response,
            message,
        )


class _NearTheMoneyChain:
    """One near-the-money snapshot: nearest expiration's first-order
    greeks entries, keyed by (strike, right) for open-interest lookup."""

    def __init__(self, expiration: date, entries: list[dict[str, Any]]) -> None:
        self.expiration = expiration
        self.entries = entries


_QueueEventT = TypeVar("_QueueEventT")


class ThetaStreamHub:
    """Owns the single, real WebSocket connection to Theta Terminal's
    streaming endpoint (ws://127.0.0.1:25520/v1/events) and dispatches
    every message -- option TRADE, option QUOTE, and underlying
    (stock/index) TRADE -- to whichever of the 3 logical consumers it
    belongs to: WhaleAlertsEngine (option trades + cumulative volume,
    the old ThetaTradeStream), Lee-Ready's quote rule (option quotes,
    the old ThetaQuoteStream), and the underlying's own live price
    (StreamUnderlyingPriceUseCase, the old ThetaUnderlyingTradeStream).

    Replaces the previous design of 3 separate classes each opening its
    own WebSocket connection to this same local endpoint. ThetaData's
    own docs (Streaming/Getting-Started) are explicit that endpoint
    supports exactly one connection: "You cannot have multiple
    connections to this endpoint... There should be a single connection
    to this endpoint in which you receive all messages send by Theta
    Data. It is up to the user to distribute these messages to other
    threads or processes themselves." Confirmed live, 2026-09-09 (real
    market open): running 3 connections against that documented
    restriction produced a sustained, self-perpetuating ~20-25s
    silence/reconnect cascade across all 3 streams -- 170+ forced
    watchdog reconnects and 6,693 explicit subscription rejections
    (`response='ERROR'`) in a single session, worst on the one stream
    (option TRADE) that had no data-silence watchdog at the time, which
    was the direct cause of a full session with zero Whale Alerts. This
    class is the documented fix: one real connection, exactly as
    ThetaData specifies, with this backend doing its own message
    distribution in-process instead of opening more connections.

    ES (UnderlyingKind.FUTURE) is deliberately never registered via
    register_symbol() -- no ThetaData futures trade-stream documentation
    was found, the same "confirmed gap, not silently guessed" precedent
    get_daily_bars already sets for futures (no working EOD REST endpoint
    either).

    Message routing: `header.type` picks QUOTE vs TRADE vs STATUS. A
    TRADE message is further routed by `contract.security_type`
    ("OPTION" vs "STOCK"/"INDEX") -- Theta Terminal broadcasts every
    subscribed symbol/contract's messages to every connected client
    regardless of what that specific connection itself subscribed to
    (confirmed live, 2026-09-03, back when this was still 3 connections
    -- see _handle_underlying_trade's own comment), so `contract.root`
    alone can't tell an option trade apart from its underlying's own
    trade when they share a root (e.g. a VIX option vs the VIX index
    itself) -- security_type is what actually distinguishes them.

    Same reconnection hardening as before (STATUS heartbeat +
    exponential backoff with reset -- see STABLE_CONNECTION_RESET_
    SECONDS), just applied to the one connection instead of 3. The
    data-silence watchdog now tracks 3 independent "last real message"
    timestamps, one per logical stream, against the same
    DATA_SILENCE_THRESHOLD_SECONDS -- any one of them going quiet while
    the connection is otherwise busy is still worth forcing a reconnect
    over, even though there's now only ever one connection to reconnect.

    Producer/consumer, strictly: `_consume()` only ever reads from the
    socket and fans each message out to a subscriber queue -- it never
    does any per-message processing of its own (Lee-Ready, Whale Alerts
    accumulation, chart persistence all live downstream, in whichever
    task actually drains that queue). `_reconcile()` runs on its own
    independent task (_run_reconcile_loop), not inline in this loop --
    confirmed live, 2026-09-09 into 2026-09-10, that leaving it inline
    reliably blocked `websocket.recv()` for 44-55s every ~20 minutes
    (300%+ over STATUS_STALE_AFTER_SECONDS), which stalls all 3 logical
    streams at once, not just option TRADE. Fan-out itself always uses
    `put_nowait()` (confirmed non-blocking, see DISPATCH_SLOW_THRESHOLD_
    SECONDS) onto a *bounded* queue: TRADE/QUOTE use
    `_dispatch_critical()`, which logs a CRITICAL and drops the message
    if the queue is genuinely full (should never happen under normal
    load -- a real signal something downstream is stuck, not something
    to silently absorb); the underlying-price queue uses
    `_dispatch_dropping()`, which drops the *oldest* queued price
    instead, since only the latest one has any value for a 0DTE chart.
    """

    def __init__(self, ws_url: str, rest_client: httpx.Client) -> None:
        self._ws_url = ws_url
        self._rest_client = rest_client
        self._contracts: dict[str, tuple[str, date, ContractType, Decimal]] = {}
        self._cumulative_volume: dict[str, int] = {}
        self._symbols: dict[str, UnderlyingKind] = {}
        self._trade_subscribers: dict[str, list[asyncio.Queue[FlowEvent]]] = {}
        self._quote_subscribers: dict[str, list[asyncio.Queue[QuoteEvent]]] = {}
        self._underlying_subscribers: dict[str, list[asyncio.Queue[UnderlyingTradeEvent]]] = {}
        self._task: asyncio.Task[None] | None = None
        self._next_request_id = 1
        self._reconciled_at: datetime | None = None
        # See _run_reconcile_loop's own docstring for why reconcile()
        # runs here instead of inline in _consume().
        self._reconcile_task: asyncio.Task[None] | None = None
        # See QUEUE_DEPTH_LOG_INTERVAL_SECONDS' own module-level comment.
        self._queue_depths_logged_at: datetime | None = None
        # Set by start() (always called from the event loop thread) and by
        # _connect_and_consume() while a connection is live -- together,
        # what request_reconnect() needs to nudge an already-running
        # connection from a *different* thread (get_option_chain() runs
        # via asyncio.to_thread in the scheduler's worker pool). See
        # request_reconnect()'s own docstring for why this, not a live
        # subscribe over the open connection.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._active_websocket: websockets.ClientConnection | None = None
        # Data-silence watchdog state, one timestamp per logical stream
        # -- see DATA_SILENCE_THRESHOLD_SECONDS' own module-level comment
        # for why this exists. None means "still warming up on the
        # current connection", never "silent" -- a fresh connection is
        # never judged before it's had a real chance to receive anything
        # of that particular kind.
        self._last_quote_at: float | None = None
        self._last_option_trade_at: float | None = None
        self._last_underlying_trade_at: float | None = None
        self._watchdog_task: asyncio.Task[None] | None = None

    def register_contract(
        self,
        occ_symbol: str,
        root: str,
        expiration: date,
        contract_type: ContractType,
        strike: Decimal,
    ) -> None:
        self._contracts[occ_symbol] = (root, expiration, contract_type, strike)
        self._cumulative_volume.setdefault(occ_symbol, 0)

    def register_symbol(self, symbol: str, kind: UnderlyingKind) -> None:
        self._symbols[symbol.upper()] = kind

    def has_contract(self, occ_symbol: str) -> bool:
        return occ_symbol in self._contracts

    def cumulative_volume(self, occ_symbol: str) -> int:
        return self._cumulative_volume.get(occ_symbol, 0)

    def start(self) -> None:
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run())
        self._watchdog_task = asyncio.create_task(self._watch_for_data_silence())
        self._reconcile_task = asyncio.create_task(self._run_reconcile_loop())

    async def stop(self) -> None:
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
            self._watchdog_task = None
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()
            try:
                await self._reconcile_task
            except asyncio.CancelledError:
                pass
            self._reconcile_task = None
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def subscribe_trade_queue(self, underlying: str) -> asyncio.Queue[FlowEvent]:
        queue: asyncio.Queue[FlowEvent] = asyncio.Queue(maxsize=TRADE_QUEUE_MAXSIZE)
        self._trade_subscribers.setdefault(underlying.upper(), []).append(queue)
        return queue

    def subscribe_quote_queue(self, underlying: str) -> asyncio.Queue[QuoteEvent]:
        queue: asyncio.Queue[QuoteEvent] = asyncio.Queue(maxsize=QUOTE_QUEUE_MAXSIZE)
        self._quote_subscribers.setdefault(underlying.upper(), []).append(queue)
        return queue

    def subscribe_underlying_queue(self, underlying: str) -> asyncio.Queue[UnderlyingTradeEvent]:
        queue: asyncio.Queue[UnderlyingTradeEvent] = asyncio.Queue(maxsize=UNDERLYING_QUEUE_MAXSIZE)
        self._underlying_subscribers.setdefault(underlying.upper(), []).append(queue)
        return queue

    def request_reconnect(self) -> None:
        """Nudge the single connection to reconnect -- same mechanism the
        3 separate classes each used to have on themselves, now shared:
        near-the-money widening (see ThetaDataProvider.get_option_chain())
        and the data-silence watchdog below both call this as their
        recovery action.

        Deliberately a reconnect, not a live SUBSCRIBE sent over the
        existing connection: _connect_and_consume() only ever subscribes
        once, right after connecting -- teaching it to also accept a live
        subscribe mid-connection is real additional surface for
        comparatively little gain, since the existing reconnect loop
        (_run()) already has robust, tested backoff/retry behavior this
        reuses as-is.

        Safe to call from any thread: get_option_chain() (one caller)
        runs in a worker thread via asyncio.to_thread, not the event loop
        thread that owns `_active_websocket` -- run_coroutine_threadsafe
        is what makes closing it from there safe. A no-op if the stream
        was never started (API process's own dormant ThetaDataProvider
        instance -- see backend/main.py's lifespan) or has no live
        connection at this exact moment (already mid-reconnect).
        """
        if self._loop is None or self._active_websocket is None:
            return
        asyncio.run_coroutine_threadsafe(self._active_websocket.close(), self._loop)

    async def _watch_for_data_silence(self) -> None:
        """Independent of _run()'s own reconnect loop -- runs for this
        object's whole lifetime, transparent to whichever connection
        happens to be live underneath it at any moment. Checks all 3
        logical streams each tick; only one reconnect per tick even if
        more than one is silent, since forcing the single shared
        connection closed is the same recovery action regardless of
        which stream(s) noticed -- nothing extra to gain from firing more
        than once in the same tick.
        """
        while True:
            await asyncio.sleep(DATA_SILENCE_CHECK_INTERVAL_SECONDS)
            if not is_market_open(utc_now()):
                continue
            now = time.monotonic()
            for name, attr in (
                ("QUOTE", "_last_quote_at"),
                ("option TRADE", "_last_option_trade_at"),
                ("underlying TRADE", "_last_underlying_trade_at"),
            ):
                last_at = getattr(self, attr)
                if last_at is None:
                    continue
                silence = now - last_at
                if silence > DATA_SILENCE_THRESHOLD_SECONDS:
                    logger.warning(
                        "ThetaStreamHub: no %s message in %.0fs during market hours -- "
                        "forcing a reconnect",
                        name,
                        silence,
                    )
                    self.request_reconnect()
                    setattr(self, attr, now)
                    break

    async def _run_reconcile_loop(self) -> None:
        """Runs reconcile() on its own schedule, independent of
        `_consume()`'s own read loop and of whichever connection happens
        to be live underneath it -- see the class docstring for why this
        exists, and the reconcile-related module-level comment above
        TRADE_QUEUE_MAXSIZE for the confirmed live evidence this fixes.
        Started once by start() and never tied to a reconnect, the same
        way _watch_for_data_silence() already is.

        A single cycle's own failure is caught and logged here (in
        addition to _reconcile()'s own per-contract try/except) so that
        one unexpected error can never silently end this periodic job
        for the rest of the process's life -- the same class of bug
        already fixed once in this incident for
        WhaleAlertsStreamManager/UnderlyingPriceStreamManager's own
        per-symbol consumer tasks.
        """
        while True:
            await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
            try:
                started_at = time.perf_counter()
                await asyncio.to_thread(self._reconcile)
                elapsed = time.perf_counter() - started_at
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ThetaStreamHub: reconcile() cycle failed unexpectedly")
                continue
            logger.info(
                "ThetaStreamHub: reconcile() took %.2fs for %d contract(s)",
                elapsed,
                len(self._contracts),
            )
            if elapsed > RECONCILE_DANGEROUS_THRESHOLD_SECONDS:
                logger.warning(
                    "ThetaStreamHub: reconcile() took %.2fs, over %.0f%% of "
                    "STATUS_STALE_AFTER_SECONDS (%ss) -- on its own task now, so this "
                    "no longer delays websocket.recv(), but a REST call this slow is "
                    "still worth investigating on its own",
                    elapsed,
                    (elapsed / STATUS_STALE_AFTER_SECONDS) * 100,
                    STATUS_STALE_AFTER_SECONDS,
                )
            self._reconciled_at = utc_now()

    async def _run(self) -> None:
        delay = RECONNECT_BASE_DELAY_SECONDS
        while True:
            connected_at = time.monotonic()
            try:
                await self._connect_and_consume()
            except asyncio.CancelledError:
                raise
            except Exception:
                # See STABLE_CONNECTION_RESET_SECONDS's own module-level
                # comment: _connect_and_consume() never returns normally
                # (only ever raises), so this is the only place that can
                # tell "was the connection we just lost actually stable
                # for a while" from "this is a fresh failure right after
                # the last one" -- the two need different treatment, not
                # the same ever-climbing delay regardless of which.
                if time.monotonic() - connected_at >= STABLE_CONNECTION_RESET_SECONDS:
                    delay = RECONNECT_BASE_DELAY_SECONDS
                logger.exception("ThetaData stream disconnected, reconnecting in %ss", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY_SECONDS)

    async def _connect_and_consume(self) -> None:
        async with websockets.connect(self._ws_url) as websocket:
            self._active_websocket = websocket
            try:
                await self._consume(websocket)
            finally:
                self._active_websocket = None

    async def _consume(self, websocket: websockets.ClientConnection) -> None:
        for root, expiration, contract_type, strike in self._contracts.values():
            await self._subscribe_option(websocket, root, expiration, contract_type, strike, "TRADE")
            await self._subscribe_option(websocket, root, expiration, contract_type, strike, "QUOTE")
        for symbol, kind in self._symbols.items():
            await self._subscribe_underlying(websocket, symbol, kind)

        last_status_at = utc_now()
        queue_depths_logged_at = self._queue_depths_logged_at or utc_now()
        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.recv(), timeout=STATUS_STALE_AFTER_SECONDS
                )
            except TimeoutError as exc:
                raise ConnectionError(
                    "No message from Theta Terminal within heartbeat window"
                ) from exc
            # Everything from here to the next websocket.recv() call is
            # processing time this connection is unavailable for -- see
            # LOOP_ITERATION_SLOW_THRESHOLD_SECONDS' own module-level
            # comment for why this is measured as one span rather than
            # trusting that no future addition to this loop can ever
            # block it.
            iteration_started_at = time.perf_counter()
            message = json.loads(raw)
            header = message.get("header", {})
            status = header.get("status")
            msg_type = header.get("type")
            if msg_type == "STATUS":
                if status != "CONNECTED":
                    raise ConnectionError(f"Theta Terminal reported status: {status}")
                last_status_at = utc_now()
            elif msg_type == "QUOTE":
                # Recorded before any filtering -- the watchdog's question
                # is "is Theta Terminal sending this connection QUOTE
                # messages at all", not "are any of them ours".
                self._last_quote_at = time.monotonic()
                self._handle_quote(message)
            elif msg_type == "TRADE":
                # security_type is what tells an option trade apart from
                # its underlying's own trade when they share a root --
                # see the class docstring. Recorded before any further
                # filtering, same reasoning as QUOTE above.
                security_type = message.get("contract", {}).get("security_type")
                if security_type == "OPTION":
                    self._last_option_trade_at = time.monotonic()
                    self._handle_option_trade(message)
                elif security_type in ("STOCK", "INDEX"):
                    self._last_underlying_trade_at = time.monotonic()
                    self._handle_underlying_trade(message)
            elif msg_type == "REQ_RESPONSE":
                _log_req_response("ThetaStreamHub", message)

            now = utc_now()
            if (now - last_status_at).total_seconds() > STATUS_STALE_AFTER_SECONDS:
                raise ConnectionError("Heartbeat stale — no STATUS message recently")
            if (now - queue_depths_logged_at).total_seconds() > QUEUE_DEPTH_LOG_INTERVAL_SECONDS:
                self._log_queue_depths()
                queue_depths_logged_at = now
                self._queue_depths_logged_at = now

            iteration_elapsed = time.perf_counter() - iteration_started_at
            if iteration_elapsed > LOOP_ITERATION_SLOW_THRESHOLD_SECONDS:
                logger.warning(
                    "ThetaStreamHub: one loop iteration (message dispatch + "
                    "housekeeping) took %.1fms before returning to websocket.recv() "
                    "-- this delays every logical stream equally, since they all "
                    "share the one connection",
                    iteration_elapsed * 1000,
                )

    async def _subscribe_option(
        self,
        websocket: websockets.ClientConnection,
        root: str,
        expiration: date,
        contract_type: ContractType,
        strike: Decimal,
        req_type: str,
    ) -> None:
        payload = {
            "msg_type": "STREAM",
            "sec_type": "OPTION",
            "req_type": req_type,
            "add": True,
            "id": self._next_request_id,
            "contract": {
                "root": root,
                "expiration": int(expiration.strftime("%Y%m%d")),
                "strike": int(strike * 1000),
                "right": "C" if contract_type == ContractType.CALL else "P",
            },
        }
        self._next_request_id += 1
        await websocket.send(json.dumps(payload))

    async def _subscribe_underlying(
        self, websocket: websockets.ClientConnection, symbol: str, kind: UnderlyingKind
    ) -> None:
        # Exact shape per ThetaData's docs -- a stock/index's "contract"
        # is just its root symbol, unlike an option's expiration/strike/
        # right. sec_type is the only field that differs between the
        # stock and index variants of this stream.
        sec_type = "INDEX" if kind == UnderlyingKind.INDEX else "STOCK"
        payload = {
            "msg_type": "STREAM",
            "sec_type": sec_type,
            "req_type": "TRADE",
            "add": True,
            "id": self._next_request_id,
            "contract": {"root": symbol},
        }
        self._next_request_id += 1
        await websocket.send(json.dumps(payload))

    def _timed_dispatch(
        self,
        queues: list[asyncio.Queue[_QueueEventT]],
        kind: str,
        put_one: Callable[[asyncio.Queue[_QueueEventT]], None],
    ) -> None:
        """Shared timing/logging wrapper around one message's fan-out --
        `_dispatch_critical`/`_dispatch_dropping` each pass their own
        per-queue put strategy as `put_one`. `put_nowait()` itself is
        synchronous and cannot block on its own (see
        DISPATCH_SLOW_THRESHOLD_SECONDS' own module-level comment) --
        this measures that directly, live, rather than asserting it from
        reading the code alone."""
        if not queues:
            return
        started_at = time.perf_counter()
        for queue in queues:
            put_one(queue)
        elapsed = time.perf_counter() - started_at
        if elapsed > DISPATCH_SLOW_THRESHOLD_SECONDS:
            logger.warning(
                "ThetaStreamHub: dispatching one %s event to %d queue(s) took %.1fms -- "
                "put_nowait() should never block; investigate if this recurs",
                kind,
                len(queues),
                elapsed * 1000,
            )

    def _dispatch_critical(
        self, queues: list[asyncio.Queue[_QueueEventT]], event: _QueueEventT, kind: str
    ) -> None:
        """For TRADE/QUOTE -- losing one of these silently is not
        acceptable (Whale Alerts and Lee-Ready both depend on seeing
        every real message). A bounded queue genuinely filling up should
        never happen under normal load (see TRADE_QUEUE_MAXSIZE/
        QUOTE_QUEUE_MAXSIZE's own module-level comment for the sizing) --
        if it does, that's a real signal something downstream is stuck,
        not something to quietly drop and move on from, so this logs at
        CRITICAL and still drops the one message (put_nowait() has
        nowhere else to put it -- the alternative, awaiting a free slot,
        is exactly the blocking-the-shared-loop bug this fan-out must
        never reintroduce)."""

        def put_one(queue: asyncio.Queue[_QueueEventT]) -> None:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.critical(
                    "ThetaStreamHub: %s queue is full (maxsize=%d) -- dropping one "
                    "message. This should never happen under normal load; a consumer "
                    "is stuck or falling behind.",
                    kind,
                    queue.maxsize,
                )

        self._timed_dispatch(queues, kind, put_one)

    def _dispatch_dropping(
        self, queues: list[asyncio.Queue[_QueueEventT]], event: _QueueEventT, kind: str
    ) -> None:
        """For the underlying-price queue only -- a stale price tick has
        no operational value once a newer one exists for a 0DTE chart
        (see UNDERLYING_QUEUE_MAXSIZE's own module-level comment), so on
        overflow this drops the OLDEST queued item (a plain
        `get_nowait()`, which -- like the rest of `asyncio.Queue` -- is
        FIFO, so the remaining items keep their original relative order)
        instead of the newest, and doesn't alert: this is the intended,
        designed-for behavior, not a symptom of anything being wrong."""

        def put_one(queue: asyncio.Queue[_QueueEventT]) -> None:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                queue.get_nowait()
                queue.put_nowait(event)

        self._timed_dispatch(queues, kind, put_one)

    def _log_queue_depths(self) -> None:
        """Real evidence for whether any subscriber queue is growing
        unbounded (a consumer falling behind), not just plausible in
        theory -- see QUEUE_DEPTH_LOG_INTERVAL_SECONDS' own module-level
        comment. No such capture existed for the 2026-09-09 incident
        itself, so this can only speak to what happens from here
        forward. Reports depth as a percentage of each queue's own
        maxsize too -- an early-warning trend, not just a snapshot,
        since _dispatch_critical's own CRITICAL log only fires once a
        queue has already actually filled up."""
        for label, subscribers in (
            ("trade", self._trade_subscribers),
            ("quote", self._quote_subscribers),
            ("underlying", self._underlying_subscribers),
        ):
            all_queues = [queue for queues in subscribers.values() for queue in queues]
            if not all_queues:
                continue
            depths = [queue.qsize() for queue in all_queues]
            fullest = max(all_queues, key=lambda queue: queue.qsize())
            fullest_pct = (
                (fullest.qsize() / fullest.maxsize) * 100 if fullest.maxsize else 0.0
            )
            logger.info(
                "ThetaStreamHub queue depths (%s): queues=%d max=%d total=%d "
                "fullest=%.1f%% of maxsize=%d",
                label,
                len(depths),
                max(depths),
                sum(depths),
                fullest_pct,
                fullest.maxsize,
            )

    def _handle_quote(self, message: dict[str, Any]) -> None:
        contract = message.get("contract", {})
        quote = message.get("quote", {})
        root = contract.get("root")
        expiration_raw = contract.get("expiration")
        strike_raw = contract.get("strike")
        right = contract.get("right")
        bid = quote.get("bid")
        ask = quote.get("ask")
        if (
            root is None
            or expiration_raw is None
            or strike_raw is None
            or bid is None
            or ask is None
        ):
            return
        expiration_digits = str(expiration_raw)
        expiration = date(
            int(expiration_digits[:4]), int(expiration_digits[4:6]), int(expiration_digits[6:8])
        )
        contract_type = ContractType.CALL if right == "C" else ContractType.PUT
        strike = Decimal(strike_raw) / Decimal(1000)
        occ_symbol = _build_occ_symbol(root, expiration, contract_type, strike)

        self._dispatch_critical(
            self._quote_subscribers.get(root.upper(), []),
            QuoteEvent(
                symbol=root.upper(),
                occ_symbol=occ_symbol,
                as_of=utc_now(),
                bid=Decimal(str(bid)),
                ask=Decimal(str(ask)),
            ),
            "QUOTE",
        )

    def _handle_option_trade(self, message: dict[str, Any]) -> None:
        contract = message.get("contract", {})
        trade = message.get("trade", {})
        root = contract.get("root")
        expiration_raw = contract.get("expiration")
        strike_raw = contract.get("strike")
        right = contract.get("right")
        size = trade.get("size")
        sequence = trade.get("sequence")
        if root is None or expiration_raw is None or strike_raw is None or size is None:
            return
        expiration_digits = str(expiration_raw)
        expiration = date(
            int(expiration_digits[:4]), int(expiration_digits[4:6]), int(expiration_digits[6:8])
        )
        contract_type = ContractType.CALL if right == "C" else ContractType.PUT
        strike = Decimal(strike_raw) / Decimal(1000)
        occ_symbol = _build_occ_symbol(root, expiration, contract_type, strike)
        self._cumulative_volume[occ_symbol] = self._cumulative_volume.get(occ_symbol, 0) + int(size)
        logger.debug("Trade stream message for %s: size=%s sequence=%s", occ_symbol, size, sequence)

        price = trade.get("price")
        # `stream_trades` (IDataProvider) has no consumer anywhere in this
        # codebase yet (confirmed before writing this adapter) — raw OPRA
        # trade ticks carry no buy/sell-aggressor or sweep/block/unusual
        # classification of their own, so this reports every tick as
        # FlowEventType.UNUSUAL / Side.UNKNOWN as an honest placeholder,
        # not a real classification. Revisit once something consumes it.
        if price is not None:
            self._dispatch_critical(
                self._trade_subscribers.get(root.upper(), []),
                FlowEvent(
                    symbol=root.upper(),
                    occ_symbol=occ_symbol,
                    as_of=utc_now(),
                    event_type=FlowEventType.UNUSUAL,
                    premium=Decimal(str(price)) * Decimal(size) * Decimal(100),
                    size=int(size),
                    aggressor_side=Side.UNKNOWN,
                ),
                "option TRADE",
            )

    def _handle_underlying_trade(self, message: dict[str, Any]) -> None:
        contract = message.get("contract", {})
        trade = message.get("trade", {})
        root = contract.get("root")
        price = trade.get("price")
        size = trade.get("size")
        if root is None or price is None or size is None:
            return
        symbol = root.upper()
        # Already routed here as security_type in (STOCK, INDEX) by
        # _consume(), but that alone doesn't confirm it matches THIS
        # symbol's own registered kind (e.g. a STOCK-shaped trade for a
        # root only ever registered as INDEX would still reach here) --
        # confirmed live, 2026-09-03, back when this was still a
        # dedicated connection: an OPTION trade sharing this root (e.g. a
        # VIX call/put) used to leak in from the separate Trade Stream
        # connection on the same Terminal and get published as if it were
        # the underlying's own price (option premiums ~$0.40-$1.57 vs
        # VIX's real ~$14.87-$14.89 at the same moment) before this check
        # existed. See TestUnderlyingTradeStream's own docstring in
        # tests/test_thetadata_provider.py.
        kind = self._symbols.get(symbol)
        if kind is None:
            return
        expected_security_type = "INDEX" if kind == UnderlyingKind.INDEX else "STOCK"
        if contract.get("security_type") != expected_security_type:
            return
        self._dispatch_dropping(
            self._underlying_subscribers.get(symbol, []),
            UnderlyingTradeEvent(
                symbol=symbol,
                as_of=utc_now(),
                price=Decimal(str(price)),
                size=int(size),
            ),
            "underlying TRADE",
        )

    def _reconcile(self) -> None:
        for occ_symbol, (root, expiration, contract_type, strike) in self._contracts.items():
            try:
                response = self._rest_client.get(
                    "/v3/option/history/ohlc",
                    params={
                        "symbol": root,
                        "expiration": expiration.strftime("%Y-%m-%d"),
                        "strike": f"{strike:.2f}",
                        "right": "call" if contract_type == ContractType.CALL else "put",
                        "interval": "1m",
                        "date": datetime.now(EASTERN_TIME).date().strftime("%Y-%m-%d"),
                        "format": "json",
                    },
                )
                response.raise_for_status()
                bars = response.json().get("response", [])
                rest_volume = sum(
                    bar.get("volume", 0) for entry in bars for bar in entry.get("data", [])
                )
            except (httpx.HTTPError, ValueError):
                logger.exception("Reconciliation REST call failed for %s", occ_symbol)
                continue

            stream_volume = self._cumulative_volume.get(occ_symbol, 0)
            if rest_volume == 0:
                continue
            discrepancy = abs(stream_volume - rest_volume) / rest_volume
            if discrepancy > 0.10:
                logger.warning(
                    "Trade stream volume reconciliation mismatch for %s: "
                    "stream=%d REST=%d (%.1f%% discrepancy)",
                    occ_symbol,
                    stream_volume,
                    rest_volume,
                    discrepancy * 100,
                )
            else:
                logger.info(
                    "Trade stream volume reconciled for %s: stream=%d REST=%d",
                    occ_symbol,
                    stream_volume,
                    rest_volume,
                )


class ThetaDataProvider:
    """Real IDataProvider adapter backed by a local Theta Terminal v3.

    See the module-level comment above for the confirmed data source per
    field, and every documented gap (Stocks/Indices subscription not
    active, futures daily bars, skew_25d approximation).
    """

    def __init__(
        self,
        rest_base_url: str,
        ws_url: str,
        request_slots: PostgresThetaRequestSlots | InProcessThetaRequestSlots | None = None,
    ) -> None:
        self._client = httpx.Client(base_url=rest_base_url, timeout=10.0)
        self._hub = ThetaStreamHub(ws_url, self._client)
        # Defaults to the pre-existing in-process behavior (correct on
        # its own whenever nothing in a separate OS process could also
        # be calling ThetaData -- every caller that doesn't pass a real
        # PostgresThetaRequestSlots explicitly, today including every
        # test) -- see request_slots.py's own module docstring for why
        # a plain threading.Semaphore stopped being enough once a
        # second process might exist.
        self._request_slots = request_slots or InProcessThetaRequestSlots(
            THETADATA_MAX_CONCURRENT_REQUESTS
        )
        self._rate_cache: tuple[date, Decimal] | None = None
        # ATR (and therefore the near-the-money width derived from it)
        # only changes once a *closed* trading day is added to the
        # history — recomputing it on every ~30s poll would be pure
        # waste, so it's cached per symbol per day, same pattern as
        # `_rate_cache` above.
        self._width_cache: dict[str, tuple[date, Decimal]] = {}
        # See NEAR_THE_MONEY_CACHE_TTL_SECONDS/OPEN_INTEREST_CACHE_TTL_SECONDS
        # above for why these exist and use different TTLs.
        self._near_the_money_cache: dict[tuple[str, date | None], tuple[float, _NearTheMoneyChain]] = {}
        self._open_interest_cache: dict[
            tuple[str, date], tuple[float, dict[tuple[Decimal, str], int]]
        ] = {}
        self._daily_bars_cache: dict[tuple[str, int], tuple[float, list[DailyBar]]] = {}

    async def start(self) -> None:
        # Registered unconditionally for every active symbol (except
        # futures — see ThetaStreamHub's own docstring for
        # why) — unlike the options streams below, this doesn't depend
        # on near-the-money chain discovery succeeding (a stock/index's
        # own price stream needs nothing more than its root symbol).
        for underlying in ACTIVE_UNDERLYINGS_BY_SYMBOL.values():
            if underlying.kind == UnderlyingKind.FUTURE:
                continue
            self._hub.register_symbol(underlying.symbol, underlying.kind)

        for symbol in ACTIVE_UNDERLYINGS_BY_SYMBOL:
            try:
                chain = self._fetch_near_the_money(symbol, expiration=None)
            except (httpx.HTTPError, ValueError):
                logger.exception(
                    "Failed to discover near-the-money contracts for %s at startup", symbol
                )
                continue
            for entry in chain.entries:
                contract_meta = entry["contract"]
                # The actual root this entry came from — for SPX/NDX
                # (see _roots_for_symbol) this can be the weekly root
                # (e.g. "SPXW"), not the outer `symbol`. Registering with
                # the wrong root would subscribe to a different contract
                # entirely on an overlapping expiration (both roots list
                # real, independent contracts there — see
                # _roots_for_symbol's docstring), not just mislabel one.
                root = contract_meta["symbol"]
                right = contract_meta["right"]
                contract_type = ContractType.CALL if right == "CALL" else ContractType.PUT
                strike = Decimal(str(contract_meta["strike"]))
                occ_symbol = _build_occ_symbol(root, chain.expiration, contract_type, strike)
                self._hub.register_contract(
                    occ_symbol, root, chain.expiration, contract_type, strike
                )
        self._hub.start()

    async def stop(self) -> None:
        await self._hub.stop()
        self._client.close()

    def _get_json(self, path: str, **params: object) -> dict[str, Any]:
        # The one chokepoint every REST call passes through — bounding
        # it here covers get_option_chain, get_underlying_snapshot,
        # get_daily_bars, and the open-interest/rate lookups uniformly,
        # without touching each of them individually.
        with self._request_slots.hold():
            response = self._client.get(path, params=params)
        if response.status_code != 200:
            raise RuntimeError(
                f"ThetaData request failed: GET {path} {params} -> "
                f"{response.status_code} {response.text}"
            )
        return response.json()

    def _get_json_allow_no_data(self, path: str, **params: object) -> dict[str, Any]:
        """Same as `_get_json`, except ThetaData's 472 ("no data found for
        your request") is treated as an empty response instead of a raised
        error. Confirmed live (2026-09 investigation): querying a SPECIFIC
        expiration for a root that simply doesn't list that date (e.g.
        `symbol=SPX, expiration=2026-09-03` — SPX's own root only has
        monthlies) returns 472, not an empty 200. For a single-root symbol
        this never matters (the caller only ever asks a root about an
        expiration it just confirmed that same root has), but for
        `_roots_for_symbol`'s two-root symbols it's an expected, normal
        outcome for one of the two roots on any given expiration — not a
        real failure — so it must not abort the whole combined fetch the
        way `_get_json` correctly does for a genuine error (auth failure,
        5xx, etc., which still raise here exactly as before)."""
        with self._request_slots.hold():
            response = self._client.get(path, params=params)
        if response.status_code == 472:
            return {"response": []}
        if response.status_code != 200:
            raise RuntimeError(
                f"ThetaData request failed: GET {path} {params} -> "
                f"{response.status_code} {response.text}"
            )
        return response.json()

    def _risk_free_rate(self) -> Decimal:
        today = datetime.now(EASTERN_TIME).date()
        if self._rate_cache is not None and self._rate_cache[0] == today:
            return self._rate_cache[1]
        end = today
        start = end - timedelta(days=7)
        body = self._get_json(
            "/v3/interest_rate/history/eod",
            symbol=RATE_SYMBOL,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            format="json",
        )
        rows = body.get("response", [])
        if not rows:
            raise RuntimeError("ThetaData interest_rate/history/eod returned no rows")
        latest = max(rows, key=lambda row: row["created"])
        rate = Decimal(str(latest["rate"])) / Decimal(100)
        self._rate_cache = (today, rate)
        return rate

    def _resolve_width(self, symbol: str, spot_price: Decimal) -> Decimal:
        today = datetime.now(EASTERN_TIME).date()
        cached = self._width_cache.get(symbol)
        if cached is not None and cached[0] == today:
            return cached[1]
        daily_bars = self.get_daily_bars(symbol)
        width = calculate_near_the_money_width(symbol, daily_bars, spot_price)
        self._width_cache[symbol] = (today, width)
        # Confirmed with the user before implementing: log the real width
        # on every fresh (once-per-symbol-per-day) computation, to check
        # against the investigation's illustrative estimate table.
        logger.info("Near-the-money width for %s: $%s (spot $%s)", symbol, width, spot_price)
        return width

    def _filter_near_the_money(
        self, symbol: str, entries: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        spot_price = Decimal(str(entries[0]["data"][0]["underlying_price"]))
        width = self._resolve_width(symbol, spot_price)
        filtered = [
            entry
            for entry in entries
            if abs(Decimal(str(entry["contract"]["strike"])) - spot_price) <= width
        ]
        if filtered:
            return filtered
        # Degenerate width (e.g. an unusually calm 14-day ATR window
        # narrower than this symbol's own strike spacing) — guarantee at
        # least the strikes immediately surrounding spot instead of
        # returning nothing.
        return sorted(
            entries,
            key=lambda entry: abs(Decimal(str(entry["contract"]["strike"])) - spot_price),
        )[:MINIMUM_NEAR_THE_MONEY_ENTRIES]

    def _fetch_near_the_money(self, symbol: str, expiration: date | None) -> _NearTheMoneyChain:
        cache_key = (symbol, expiration)
        cached = self._near_the_money_cache.get(cache_key)
        if cached is not None and time.monotonic() - cached[0] < NEAR_THE_MONEY_CACHE_TTL_SECONDS:
            return cached[1]

        expiration_param = expiration.strftime("%Y-%m-%d") if expiration else "*"
        entries: list[dict[str, Any]] = []
        for root in _roots_for_symbol(symbol):
            # _get_json_allow_no_data, not _get_json: for a two-root
            # symbol (see _roots_for_symbol) it's normal for one root to
            # simply not list a given expiration the other root does —
            # confirmed live, ThetaData answers that with a 472, not an
            # empty 200 — and that must not abort the whole combined
            # fetch just because one of the two roots has nothing here.
            body = self._get_json_allow_no_data(
                "/v3/option/snapshot/greeks/first_order",
                symbol=root,
                expiration=expiration_param,
                strike_range=NEAR_THE_MONEY_OVERFETCH_STRIKE_RANGE,
                format="json",
            )
            entries.extend(entry for entry in body.get("response", []) if entry.get("data"))
        if not entries:
            raise RuntimeError(f"ThetaData returned no near-the-money contracts for {symbol}")
        if expiration is not None:
            result = _NearTheMoneyChain(expiration, self._filter_near_the_money(symbol, entries))
        else:
            # ThetaData's snapshot endpoint keeps returning an already-
            # expired contract's last-known quote for a while after it
            # expires (confirmed live, 2026-09: SPY's 2026-09-02 —
            # yesterday relative to that check — still came back with
            # data: bid=0.0, implied_vol=6.19 (619%), the last quote
            # timestamped 16:15 the day it expired). Never filtered
            # before, so `min()` below could pick a dead contract as
            # "nearest" — this excludes anything before today first, and
            # keeps today itself (>=, not >) so a genuine 0DTE expiration
            # is never wrongly excluded.
            #
            # But "today" alone isn't enough once the market has actually
            # closed -- see _nearest_expiration_cutoff.
            cutoff = _nearest_expiration_cutoff(datetime.now(EASTERN_TIME))
            unexpired_entries = [
                entry
                for entry in entries
                if date.fromisoformat(entry["contract"]["expiration"]) >= cutoff
            ]
            if not unexpired_entries:
                raise RuntimeError(
                    f"ThetaData returned no unexpired near-the-money contracts for {symbol}"
                )
            nearest = min(
                date.fromisoformat(entry["contract"]["expiration"]) for entry in unexpired_entries
            )
            nearest_entries = [
                entry
                for entry in unexpired_entries
                if date.fromisoformat(entry["contract"]["expiration"]) == nearest
            ]
            result = _NearTheMoneyChain(nearest, self._filter_near_the_money(symbol, nearest_entries))

        self._near_the_money_cache[cache_key] = (time.monotonic(), result)
        return result

    def _fetch_open_interest(
        self, symbol: str, expiration: date
    ) -> dict[tuple[str, Decimal, str], int]:
        # Keyed by (root, strike, right), not just (strike, right) — for
        # symbols with a weekly root (see _roots_for_symbol), the same
        # strike/right can legitimately exist under both roots as two
        # separate contracts with independent open interest on a shared
        # expiration; a (strike, right)-only key would silently let one
        # root's OI overwrite the other's instead of keeping both.
        cache_key = (symbol, expiration)
        cached = self._open_interest_cache.get(cache_key)
        if cached is not None and time.monotonic() - cached[0] < OPEN_INTEREST_CACHE_TTL_SECONDS:
            return cached[1]

        result: dict[tuple[str, Decimal, str], int] = {}
        for root in _roots_for_symbol(symbol):
            # _get_json_allow_no_data, not _get_json: confirmed live this
            # returns 472 (not an empty 200) when a root has nothing at
            # this specific expiration — expected for a two-root symbol
            # whenever the resolved expiration only exists on the other
            # root (e.g. SPX itself has no 2026-09-03 listing at all, only
            # SPXW does), not a real failure. See the same reasoning in
            # _fetch_near_the_money above.
            body = self._get_json_allow_no_data(
                "/v3/option/snapshot/open_interest",
                symbol=root,
                expiration=expiration.strftime("%Y-%m-%d"),
                # Same over-fetch width as _fetch_near_the_money — every
                # contract that survives that method's price-width filter
                # must also have real open-interest data available here,
                # not a silent 0 fallback for strikes beyond the old narrow
                # range.
                strike_range=NEAR_THE_MONEY_OVERFETCH_STRIKE_RANGE,
                format="json",
            )
            for entry in body.get("response", []):
                data_points = entry.get("data") or []
                if not data_points:
                    continue
                contract_meta = entry["contract"]
                key = (
                    contract_meta["symbol"],
                    Decimal(str(contract_meta["strike"])),
                    contract_meta["right"],
                )
                result[key] = int(data_points[0]["open_interest"])

        self._open_interest_cache[cache_key] = (time.monotonic(), result)
        return result

    def _fetch_underlying_volume(self, symbol: str, kind: UnderlyingKind) -> int:
        """Session-cumulative share volume for `symbol` — confirmed live
        (2026-09 investigation) via GET /v3/stock/snapshot/ohlc (equities,
        SPY: 16,396,508) and /v3/index/snapshot/ohlc (indices — always 0,
        since an index has no share volume of its own, only its component
        stocks do). Closes the gap documented on MarketSnapshot.volume
        below: earlier investigation found no live Stocks/Indices
        subscription to source this from, but this is a plain REST
        snapshot call, unrelated to that streaming gap.

        Deliberately uncached, unlike _fetch_near_the_money/
        _fetch_open_interest above — volume changes continuously through
        the session (unlike open interest) and this is already called at
        most once per get_underlying_snapshot() invocation (unlike the
        near-the-money chain, which get_option_chain() and
        get_underlying_snapshot() both fetch back-to-back), so there is
        no repeated within-cycle call here to save.

        Falls back to 0 (this field's existing, already-handled value —
        see calculate_anchored_vwap) on any fetch failure rather than
        raising, so a transient problem with this one field doesn't fail
        the whole snapshot refresh cycle the way a raise here would.
        """
        if kind == UnderlyingKind.FUTURE:
            # No working futures OHLC snapshot endpoint confirmed — same
            # documented gap as get_daily_bars' futures case below.
            return 0
        endpoint = (
            "/v3/index/snapshot/ohlc" if kind == UnderlyingKind.INDEX else "/v3/stock/snapshot/ohlc"
        )
        try:
            body = self._get_json(endpoint, symbol=symbol, format="json")
            rows = body.get("response", [])
            if not rows:
                return 0
            return int(rows[0]["volume"])
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError):
            logger.exception("Failed to fetch underlying volume for %s", symbol)
            return 0

    def get_option_chain(self, underlying: str, expiration: date | None = None) -> OptionChain:
        symbol = underlying.upper()
        chain = self._fetch_near_the_money(symbol, expiration)
        open_interest_by_key = self._fetch_open_interest(symbol, chain.expiration)
        rate = self._risk_free_rate()
        now_et = datetime.now(EASTERN_TIME)
        time_to_expiration = _time_to_expiration_years(chain.expiration, now_et)

        spot_price: Decimal | None = None
        latest_as_of = utc_now()
        contracts = []
        # Near-the-money re-subscription, confirmed live 2026-09: the
        # Trade/Quote Stream WebSocket subscriptions are only ever
        # registered once, at ThetaDataProvider.start() (Worker startup),
        # from whatever chain was near-the-money at that moment -- nothing
        # re-discovers or widens that set as spot drifts during the
        # session. This chain fetch already recomputes "near-the-money
        # right now" every scheduler cycle (~30s, via
        # RefreshUnderlyingSnapshotUseCase -> get_option_chain), so it's
        # the natural place to also register any contract that's near-
        # the-money now but wasn't at startup -- no separate timer, no
        # distance-from-center math, just a direct membership check
        # against what's already registered. Deliberately additive only
        # (never unsubscribes a contract that drifted OUT of range) --
        # the accumulated set over one session is small and bounded (a
        # handful of strikes at most, even for a large move), and the
        # Worker's near-the-money set resets fresh on its next restart
        # anyway; unsubscribing would need to guard against tearing down
        # a contract mid-bucket in WhaleAlertsEngine's own state, for a
        # benefit (WS message volume) that isn't the real constraint here
        # (that's REST request concurrency, via theta_request_slots).
        newly_registered = False
        for entry in chain.entries:
            contract_meta = entry["contract"]
            data = entry["data"][0]
            # The actual root this entry came from — for SPX/NDX this can
            # be the weekly root (e.g. "SPXW"), which lists real,
            # independently-traded contracts, not aliases of the bare
            # root's own (see _roots_for_symbol's docstring). Used for the
            # OCC symbol and the open-interest lookup so an overlapping
            # expiration's two genuinely different contracts at the same
            # strike/right don't collide into one.
            root = contract_meta["symbol"]
            right = contract_meta["right"]
            contract_type = ContractType.CALL if right == "CALL" else ContractType.PUT
            strike = Decimal(str(contract_meta["strike"]))
            underlying_price = Decimal(str(data["underlying_price"]))
            spot_price = underlying_price
            bid = Decimal(str(data["bid"]))
            ask = Decimal(str(data["ask"]))
            iv = Decimal(str(data["implied_vol"]))
            delta = Decimal(str(data["delta"]))
            theta = Decimal(str(data["theta"]))
            vega = Decimal(str(data["vega"]))
            bsm = calculate_bsm_greeks(underlying_price, strike, rate, iv, time_to_expiration)

            occ_symbol = _build_occ_symbol(root, chain.expiration, contract_type, strike)
            if not self._hub.has_contract(occ_symbol):
                self._hub.register_contract(occ_symbol, root, chain.expiration, contract_type, strike)
                newly_registered = True
            open_interest = open_interest_by_key.get((root, strike, right), 0)
            volume = self._hub.cumulative_volume(occ_symbol)
            as_of = _parse_et_timestamp(data["timestamp"])
            latest_as_of = max(latest_as_of, as_of)

            contracts.append(
                OptionContract(
                    underlying=symbol,
                    strike=strike,
                    expiration=chain.expiration,
                    contract_type=contract_type,
                    occ_symbol=occ_symbol,
                    bid=bid,
                    ask=ask,
                    last=(bid + ask) / 2,
                    volume=volume,
                    open_interest=open_interest,
                    iv=iv,
                    greeks=OptionGreeks(
                        delta=delta,
                        gamma=bsm.gamma,
                        theta=theta,
                        vega=vega,
                        charm=bsm.charm,
                        vanna=bsm.vanna,
                    ),
                )
            )

        if newly_registered:
            logger.info("Near-the-money set for %s widened, reconnecting streams", symbol)
            self._hub.request_reconnect()

        if spot_price is None:
            raise RuntimeError(f"ThetaData returned no usable contracts for {symbol}")
        return OptionChain(
            symbol=symbol,
            as_of=latest_as_of,
            spot_price=spot_price,
            contracts=tuple(contracts),
        )

    def get_underlying_snapshot(self, underlying: str) -> MarketSnapshot:
        symbol = underlying.upper()
        chain = self._fetch_near_the_money(symbol, expiration=None)
        open_interest_by_key = self._fetch_open_interest(symbol, chain.expiration)
        active = ACTIVE_UNDERLYINGS_BY_SYMBOL.get(symbol)
        kind = active.kind if active is not None else UnderlyingKind.EQUITY
        volume = self._fetch_underlying_volume(symbol, kind)

        price: Decimal | None = None
        as_of = utc_now()
        ivs: list[Decimal] = []
        call_oi = 0
        put_oi = 0
        for entry in chain.entries:
            data = entry["data"][0]
            price = Decimal(str(data["underlying_price"]))
            as_of = _parse_et_timestamp(data["timestamp"])
            ivs.append(Decimal(str(data["implied_vol"])))
            contract_meta = entry["contract"]
            key = (
                contract_meta["symbol"],
                Decimal(str(contract_meta["strike"])),
                contract_meta["right"],
            )
            oi = open_interest_by_key.get(key, 0)
            if contract_meta["right"] == "CALL":
                call_oi += oi
            else:
                put_oi += oi

        if price is None:
            raise RuntimeError(f"ThetaData returned no usable contracts for {symbol}")

        # Approximated from the same near-the-money chain already fetched
        # above, not the true market-wide ATM IV / put-call ratio a full
        # chain would give — documented explicitly as an approximation,
        # not presented as the real thing.
        atm_iv = sum(ivs, Decimal(0)) / len(ivs) if ivs else Decimal(0)
        pc_oi_ratio = Decimal(put_oi) / Decimal(call_oi) if call_oi else Decimal(0)

        return MarketSnapshot(
            symbol=symbol,
            as_of=as_of,
            price=price,
            # Session-cumulative share volume from GET /v3/stock/snapshot/
            # ohlc (or /v3/index/snapshot/ohlc for indices, always 0 there
            # — an index has no share volume of its own). Previously
            # hardcoded 0 here (no live Stocks/Indices subscription to
            # source it from) — that gap is closed by _fetch_underlying_
            # volume above, a plain REST snapshot call unrelated to that
            # streaming limitation. calculate_anchored_vwap already
            # expects exactly this session-cumulative-with-reset-at-9:30
            # shape (see its own docstring), so wiring a real value in
            # here doesn't change its contract, only lets it stop being
            # permanently provisional.
            volume=volume,
            pc_oi_ratio=pc_oi_ratio,
            # No 25-delta strikes in the near-the-money range this
            # adapter fetches — computing a real skew would need
            # additional, out-of-scope contracts.
            skew_25d=Decimal(0),
            atm_iv=atm_iv,
        )

    def get_daily_bars(self, underlying: str, days: int = 20) -> list[DailyBar]:
        symbol = underlying.upper()
        active = ACTIVE_UNDERLYINGS_BY_SYMBOL.get(symbol)
        if active is not None and active.kind == UnderlyingKind.FUTURE:
            # No working futures EOD endpoint was found (confirmed 404
            # against /v3/future/history/eod) — documented gap, same
            # pattern as the other known limitations above.
            return []
        cache_key = (symbol, days)
        cached = self._daily_bars_cache.get(cache_key)
        if cached is not None and time.monotonic() - cached[0] < DAILY_BARS_CACHE_TTL_SECONDS:
            return cached[1]
        endpoint = (
            "/v3/index/history/eod"
            if active is not None and active.kind == UnderlyingKind.INDEX
            else "/v3/stock/history/eod"
        )
        end = datetime.now(EASTERN_TIME).date()
        start = end - timedelta(days=days * 2)  # buffer for weekends/holidays
        body = self._get_json(
            endpoint,
            symbol=symbol,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            format="json",
        )
        bars = []
        for row in body.get("response", [])[-days:]:
            bar_date = _parse_et_timestamp(row["last_trade"]).date()
            bars.append(
                DailyBar(
                    symbol=symbol,
                    date=bar_date,
                    open_price=Decimal(str(row["open"])),
                    high=Decimal(str(row["high"])),
                    low=Decimal(str(row["low"])),
                    close=Decimal(str(row["close"])),
                )
            )
        self._daily_bars_cache[cache_key] = (time.monotonic(), bars)
        return bars

    def get_minute_bars(self, underlying: str, start: date, end: date) -> list[MinuteBar]:
        """Closed 1-minute OHLCV bars from ThetaData's `/v3/index/history/
        ohlc` (Indices Pro plan) for `underlying` between `start` and `end`
        (inclusive, calendar dates). Confirmed live, 2026-09: 391 bars for
        a single regular session (09:30-16:00 ET inclusive) on SPX/VIX/NDX,
        `volume`/`count` always 0 for an index (no share volume of its
        own — same fact `get_underlying_snapshot`'s volume handling
        already documents for indices).

        Index-only, and deliberately NOT part of `IDataProvider` — this
        exists solely for the one-time historical backfill
        (`backend/scripts/backfill_minute_history.py`), not for any live
        use case yet.
        """
        symbol = underlying.upper()
        active = ACTIVE_UNDERLYINGS_BY_SYMBOL.get(symbol)
        if active is None or active.kind != UnderlyingKind.INDEX:
            raise RuntimeError(
                f"get_minute_bars is confirmed only for index underlyings, not {symbol}"
            )
        body = self._get_json(
            "/v3/index/history/ohlc",
            symbol=symbol,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            interval="1m",
            format="json",
        )
        bars = []
        for row in body.get("response", []):
            bars.append(
                MinuteBar(
                    symbol=symbol,
                    time=_parse_et_timestamp(row["timestamp"]),
                    open_price=Decimal(str(row["open"])),
                    high=Decimal(str(row["high"])),
                    low=Decimal(str(row["low"])),
                    close=Decimal(str(row["close"])),
                    volume=int(row["volume"]),
                )
            )
        return bars

    async def stream_trades(self, underlying: str) -> AsyncIterator[FlowEvent]:
        queue = self._hub.subscribe_trade_queue(underlying)
        while True:
            yield await queue.get()

    async def stream_quotes(self, underlying: str) -> AsyncIterator[QuoteEvent]:
        queue = self._hub.subscribe_quote_queue(underlying)
        while True:
            yield await queue.get()

    async def stream_underlying_trades(self, underlying: str) -> AsyncIterator[UnderlyingTradeEvent]:
        queue = self._hub.subscribe_underlying_queue(underlying)
        while True:
            yield await queue.get()
