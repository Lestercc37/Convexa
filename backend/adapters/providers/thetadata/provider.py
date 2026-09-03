from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import websockets

from backend.domain.entities import (
    ContractType,
    DailyBar,
    FlowEvent,
    FlowEventType,
    MarketSnapshot,
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
from backend.domain.use_cases.market_hours import EASTERN_TIME, MARKET_CLOSE_ET

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

# ThetaData's real concurrency limit is per ACCOUNT, not per endpoint or
# symbol, and doesn't add up across subscriptions — the highest tier
# among them governs (confirmed against ThetaData's own docs, 2026-09
# investigation): Options Standard, our highest tier, caps concurrent
# REST requests at 4 for the whole backend. Nothing enforced this
# before — the near-zero real concurrency observed today was an
# accidental side effect of the scheduler's sequential per-symbol loop
# (backend/core/scheduler.py), not a designed safeguard, so this is
# preventive: if that loop is ever parallelized for performance, this
# still holds the real limit. `threading.Semaphore`, not
# `asyncio.Semaphore` — every REST call here runs synchronously inside
# a worker thread (via `asyncio.to_thread` from the scheduler, or
# Starlette's threadpool for the sync route handlers), never on the
# event loop itself, so an asyncio primitive wouldn't coordinate
# anything real across those threads.
THETADATA_MAX_CONCURRENT_REQUESTS = 4

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
# expirations than VIX's own root) but is deliberately NOT included
# here — reported separately, not fixed without asking, since this
# constant is scoped to exactly what was confirmed and requested.
_WEEKLY_ROOT_BY_SYMBOL: dict[str, str] = {
    "SPX": "SPXW",
    "NDX": "NDXP",
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


class ThetaTradeStream:
    """Owns the persistent WebSocket connection to Theta Terminal's Trade
    Stream and the in-memory cumulative-volume state it feeds.

    ThetaData has had two real documented incidents of disconnection/data
    loss, one during a market open — this is not optional hardening:
    - STATUS messages (roughly one per second) are the heartbeat; no
      message for `STATUS_STALE_AFTER_SECONDS` or a non-CONNECTED status
      triggers a reconnect.
    - Reconnects use exponential backoff (capped) and increment the
      request `id`, per ThetaData's own documented pattern.
    - Every message's `sequence` is logged (not acted on beyond that) as
      a gap-detection safety net — OPRA sequence numbers are global
      across all contracts/exchanges, not a simple per-contract counter,
      so this is informational, not a correctness guarantee by itself.
    - Periodic reconciliation compares the accumulated volume against
      `GET /v3/option/history/ohlc` for the same contract/day and logs a
      clear warning (never fails silently) on a large discrepancy.
    """

    def __init__(self, ws_url: str, rest_client: httpx.Client) -> None:
        self._ws_url = ws_url
        self._rest_client = rest_client
        self._contracts: dict[str, tuple[str, date, ContractType, Decimal]] = {}
        self._cumulative_volume: dict[str, int] = {}
        self._subscribers: dict[str, list[asyncio.Queue[FlowEvent]]] = {}
        self._task: asyncio.Task[None] | None = None
        self._next_request_id = 1
        self._reconciled_at: datetime | None = None

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

    def cumulative_volume(self, occ_symbol: str) -> int:
        return self._cumulative_volume.get(occ_symbol, 0)

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def subscribe_queue(self, underlying: str) -> asyncio.Queue[FlowEvent]:
        queue: asyncio.Queue[FlowEvent] = asyncio.Queue()
        self._subscribers.setdefault(underlying.upper(), []).append(queue)
        return queue

    async def _run(self) -> None:
        delay = RECONNECT_BASE_DELAY_SECONDS
        while True:
            try:
                await self._connect_and_consume()
                delay = RECONNECT_BASE_DELAY_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ThetaData trade stream disconnected, reconnecting in %ss", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY_SECONDS)

    async def _connect_and_consume(self) -> None:
        async with websockets.connect(self._ws_url) as websocket:
            for root, expiration, contract_type, strike in self._contracts.values():
                await self._subscribe(websocket, root, expiration, contract_type, strike)
            last_status_at = utc_now()
            reconciled_at = self._reconciled_at or utc_now()
            while True:
                try:
                    raw = await asyncio.wait_for(
                        websocket.recv(), timeout=STATUS_STALE_AFTER_SECONDS
                    )
                except TimeoutError as exc:
                    raise ConnectionError(
                        "No message from Theta Terminal within heartbeat window"
                    ) from exc
                message = json.loads(raw)
                header = message.get("header", {})
                status = header.get("status")
                if header.get("type") == "STATUS":
                    if status != "CONNECTED":
                        raise ConnectionError(f"Theta Terminal reported status: {status}")
                    last_status_at = utc_now()
                elif header.get("type") == "TRADE":
                    self._handle_trade(message)
                elif header.get("type") == "REQ_RESPONSE":
                    _log_req_response("ThetaTradeStream", message)

                now = utc_now()
                if (now - last_status_at).total_seconds() > STATUS_STALE_AFTER_SECONDS:
                    raise ConnectionError("Heartbeat stale — no STATUS message recently")
                if (now - reconciled_at).total_seconds() > RECONCILE_INTERVAL_SECONDS:
                    await asyncio.to_thread(self._reconcile)
                    reconciled_at = now
                    self._reconciled_at = now

    async def _subscribe(
        self,
        websocket: websockets.ClientConnection,
        root: str,
        expiration: date,
        contract_type: ContractType,
        strike: Decimal,
    ) -> None:
        payload = {
            "msg_type": "STREAM",
            "sec_type": "OPTION",
            "req_type": "TRADE",
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

    def _handle_trade(self, message: dict[str, Any]) -> None:
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
            queues = self._subscribers.get(root.upper(), [])
            for queue in queues:
                queue.put_nowait(
                    FlowEvent(
                        symbol=root.upper(),
                        occ_symbol=occ_symbol,
                        as_of=utc_now(),
                        event_type=FlowEventType.UNUSUAL,
                        premium=Decimal(str(price)) * Decimal(size) * Decimal(100),
                        size=int(size),
                        aggressor_side=Side.UNKNOWN,
                    )
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


class ThetaQuoteStream:
    """Owns a persistent WebSocket connection to Theta Terminal's Quote
    Stream and dispatches each incoming quote to subscribers, for
    Lee-Ready's quote rule (calculate_lee_ready.py, StreamWhaleAlertsUseCase).

    Confirmed against ThetaData's public v3 docs (Streaming/US-Options/
    Quote-Stream) before writing this: same `msg_type`/`sec_type`/
    `contract` shape as the Trade Stream, just `req_type: "QUOTE"` and a
    `quote` object (`bid`, `ask`, ...) instead of `trade`.

    A separate WebSocket connection from ThetaTradeStream, not one
    multiplexing both TRADE and QUOTE subscriptions (ThetaData's protocol
    does support that on a single connection) — this keeps the
    already-validated, incident-hardened Trade Stream (see its own
    docstring) completely untouched, at the cost of one extra lightweight
    connection to a local Theta Terminal process, not a rate-limited
    remote server.

    Same reconnection hardening as ThetaTradeStream (STATUS heartbeat,
    exponential backoff) — no volume reconciliation here, since quotes
    have no cumulative-volume concept to reconcile against REST.
    """

    def __init__(self, ws_url: str) -> None:
        self._ws_url = ws_url
        self._contracts: dict[str, tuple[str, date, ContractType, Decimal]] = {}
        self._subscribers: dict[str, list[asyncio.Queue[QuoteEvent]]] = {}
        self._task: asyncio.Task[None] | None = None
        self._next_request_id = 1

    def register_contract(
        self,
        occ_symbol: str,
        root: str,
        expiration: date,
        contract_type: ContractType,
        strike: Decimal,
    ) -> None:
        self._contracts[occ_symbol] = (root, expiration, contract_type, strike)

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def subscribe_queue(self, underlying: str) -> asyncio.Queue[QuoteEvent]:
        queue: asyncio.Queue[QuoteEvent] = asyncio.Queue()
        self._subscribers.setdefault(underlying.upper(), []).append(queue)
        return queue

    async def _run(self) -> None:
        delay = RECONNECT_BASE_DELAY_SECONDS
        while True:
            try:
                await self._connect_and_consume()
                delay = RECONNECT_BASE_DELAY_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ThetaData quote stream disconnected, reconnecting in %ss", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY_SECONDS)

    async def _connect_and_consume(self) -> None:
        async with websockets.connect(self._ws_url) as websocket:
            for root, expiration, contract_type, strike in self._contracts.values():
                await self._subscribe(websocket, root, expiration, contract_type, strike)
            last_status_at = utc_now()
            while True:
                try:
                    raw = await asyncio.wait_for(
                        websocket.recv(), timeout=STATUS_STALE_AFTER_SECONDS
                    )
                except TimeoutError as exc:
                    raise ConnectionError(
                        "No message from Theta Terminal within heartbeat window"
                    ) from exc
                message = json.loads(raw)
                header = message.get("header", {})
                status = header.get("status")
                if header.get("type") == "STATUS":
                    if status != "CONNECTED":
                        raise ConnectionError(f"Theta Terminal reported status: {status}")
                    last_status_at = utc_now()
                elif header.get("type") == "QUOTE":
                    self._handle_quote(message)
                elif header.get("type") == "REQ_RESPONSE":
                    _log_req_response("ThetaQuoteStream", message)

                now = utc_now()
                if (now - last_status_at).total_seconds() > STATUS_STALE_AFTER_SECONDS:
                    raise ConnectionError("Heartbeat stale — no STATUS message recently")

    async def _subscribe(
        self,
        websocket: websockets.ClientConnection,
        root: str,
        expiration: date,
        contract_type: ContractType,
        strike: Decimal,
    ) -> None:
        payload = {
            "msg_type": "STREAM",
            "sec_type": "OPTION",
            "req_type": "QUOTE",
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

        queues = self._subscribers.get(root.upper(), [])
        for queue in queues:
            queue.put_nowait(
                QuoteEvent(
                    symbol=root.upper(),
                    occ_symbol=occ_symbol,
                    as_of=utc_now(),
                    bid=Decimal(str(bid)),
                    ask=Decimal(str(ask)),
                )
            )


class ThetaUnderlyingTradeStream:
    """Owns the persistent WebSocket connection to Theta Terminal's
    per-underlying Trade Stream — the underlying's own price (e.g. SPY
    the equity, or SPX the index), not an option contract's. Feeds
    StreamUnderlyingPriceUseCase, additively: it only ever makes the
    chart's live price fresher between REST scheduler cycles, never
    replacing that scheduler for anything else.

    CONFIRMED LIVE against a real Theta Terminal, market open
    (2026-09-03, Stocks+Index plans active): SPY/TSLA (STOCK) and
    SPX/VIX/NDX (INDEX) all delivered genuine TRADE messages in exactly
    the shape assumed below, `size` confirmed always 0 for INDEX
    messages. Sources, fetched 2026-09:
    - https://docs.thetadata.us/Streaming/US-Stocks/Trade-Stream.html
    - https://docs.thetadata.us/Streaming/US-Indices/Price-Stream.html
      (confirmed: identical `trade` message shape to the stock stream,
      only the subscribe payload's `sec_type` differs — `"INDEX"`
      instead of `"STOCK"`, and `size` is always reported as 0)

    FIXED (was a live production bug, 2026-09-03): the local Theta
    Terminal broadcasts every symbol/contract with an active
    subscription ANYWHERE on that Terminal to every connected WebSocket
    client — not scoped to what this specific connection subscribed to.
    A connection that only asked for `sec_type: "INDEX"` on `"VIX"`
    still received `security_type: "OPTION"` trade messages for the
    same root (leaking in from this same backend's own `ThetaTradeStream`,
    separately subscribed to VIX's near-the-money option chain).
    `_handle_trade` used to filter only by `contract.root`, so those
    option trades were accepted and published as if they were the
    underlying's own price. Quantified live before the fix: 60% of
    root="VIX" messages over 60s were OPTION contamination (9 of 15,
    option premiums ~$0.40-$1.57 published as if they were VIX's own
    price, real VIX level ~14.87-14.89 at the same moment); ~9% for
    root="SPX" (13 of 139). `_handle_trade` now also checks
    `contract.security_type` against the registered `UnderlyingKind`
    before accepting a message. See
    test_option_trades_sharing_the_same_root_is_filtered_out and
    TestUnderlyingTradeStream's own docstring in
    tests/test_thetadata_provider.py.

    ES (UnderlyingKind.FUTURE) is deliberately never registered — no
    ThetaData futures trade-stream documentation was found, the same
    "confirmed gap, not silently guessed" precedent get_daily_bars
    already sets for futures (no working EOD REST endpoint either).

    A separate connection from ThetaTradeStream/ThetaQuoteStream (same
    reasoning already documented on those: keeps their already-hardened
    connections untouched, at the cost of one extra lightweight
    connection to a local Theta Terminal process, not a rate-limited
    remote server) — same STATUS-heartbeat/exponential-backoff hardening
    as both. Simpler than ThetaTradeStream in two ways: no reconciliation
    (there's no established intraday stock/index OHLC REST endpoint in
    this codebase to reconcile against — get_daily_bars only has *daily*
    bars), and it subscribes per underlying symbol directly, not per
    discovered option contract.
    """

    def __init__(self, ws_url: str) -> None:
        self._ws_url = ws_url
        self._symbols: dict[str, UnderlyingKind] = {}
        self._subscribers: dict[str, list[asyncio.Queue[UnderlyingTradeEvent]]] = {}
        self._task: asyncio.Task[None] | None = None
        self._next_request_id = 1

    def register_symbol(self, symbol: str, kind: UnderlyingKind) -> None:
        self._symbols[symbol.upper()] = kind

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def subscribe_queue(self, underlying: str) -> asyncio.Queue[UnderlyingTradeEvent]:
        queue: asyncio.Queue[UnderlyingTradeEvent] = asyncio.Queue()
        self._subscribers.setdefault(underlying.upper(), []).append(queue)
        return queue

    async def _run(self) -> None:
        delay = RECONNECT_BASE_DELAY_SECONDS
        while True:
            try:
                await self._connect_and_consume()
                delay = RECONNECT_BASE_DELAY_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "ThetaData underlying trade stream disconnected, reconnecting in %ss", delay
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY_SECONDS)

    async def _connect_and_consume(self) -> None:
        async with websockets.connect(self._ws_url) as websocket:
            for symbol, kind in self._symbols.items():
                await self._subscribe(websocket, symbol, kind)
            last_status_at = utc_now()
            while True:
                try:
                    raw = await asyncio.wait_for(
                        websocket.recv(), timeout=STATUS_STALE_AFTER_SECONDS
                    )
                except TimeoutError as exc:
                    raise ConnectionError(
                        "No message from Theta Terminal within heartbeat window"
                    ) from exc
                message = json.loads(raw)
                header = message.get("header", {})
                status = header.get("status")
                if header.get("type") == "STATUS":
                    if status != "CONNECTED":
                        raise ConnectionError(f"Theta Terminal reported status: {status}")
                    last_status_at = utc_now()
                elif header.get("type") == "TRADE":
                    self._handle_trade(message)
                elif header.get("type") == "REQ_RESPONSE":
                    _log_req_response("ThetaUnderlyingTradeStream", message)

                now = utc_now()
                if (now - last_status_at).total_seconds() > STATUS_STALE_AFTER_SECONDS:
                    raise ConnectionError("Heartbeat stale — no STATUS message recently")

    async def _subscribe(
        self, websocket: websockets.ClientConnection, symbol: str, kind: UnderlyingKind
    ) -> None:
        # Exact shape per ThetaData's docs (see class docstring) — a
        # stock/index's "contract" is just its root symbol, unlike an
        # option's expiration/strike/right. sec_type is the only field
        # that differs between the stock and index variants of this
        # stream — confirmed the trade message shape itself is identical.
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

    def _handle_trade(self, message: dict[str, Any]) -> None:
        contract = message.get("contract", {})
        trade = message.get("trade", {})
        root = contract.get("root")
        price = trade.get("price")
        size = trade.get("size")
        if root is None or price is None or size is None:
            return
        symbol = root.upper()
        # The local Theta Terminal broadcasts every symbol/contract with
        # an active subscription ANYWHERE on it to every connected
        # client, not scoped to what this connection itself subscribed
        # to (confirmed live, 2026-09-03 — see this class's own
        # docstring). An OPTION trade sharing this root (e.g. a VIX
        # call/put, leaking in from ThetaTradeStream's own separate
        # near-the-money subscription on the same Terminal) carries the
        # same contract.root, so root alone can't tell it apart from a
        # genuine underlying trade — contract.security_type is what
        # actually distinguishes them. Without this check, an option's
        # premium (confirmed live: VIX ~$0.40-$1.57) gets published as
        # if it were the underlying's own price (VIX ~$14.87-$14.89 at
        # the same moment).
        kind = self._symbols.get(symbol)
        if kind is None:
            return
        expected_security_type = "INDEX" if kind == UnderlyingKind.INDEX else "STOCK"
        if contract.get("security_type") != expected_security_type:
            return
        event = UnderlyingTradeEvent(
            symbol=symbol,
            as_of=utc_now(),
            price=Decimal(str(price)),
            size=int(size),
        )
        for queue in self._subscribers.get(symbol, []):
            queue.put_nowait(event)


class ThetaDataProvider:
    """Real IDataProvider adapter backed by a local Theta Terminal v3.

    See the module-level comment above for the confirmed data source per
    field, and every documented gap (Stocks/Indices subscription not
    active, futures daily bars, skew_25d approximation).
    """

    def __init__(self, rest_base_url: str, ws_url: str) -> None:
        self._client = httpx.Client(base_url=rest_base_url, timeout=10.0)
        self._stream = ThetaTradeStream(ws_url, self._client)
        self._quote_stream = ThetaQuoteStream(ws_url)
        self._underlying_trade_stream = ThetaUnderlyingTradeStream(ws_url)
        self._request_semaphore = threading.Semaphore(THETADATA_MAX_CONCURRENT_REQUESTS)
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

    async def start(self) -> None:
        # Registered unconditionally for every active symbol (except
        # futures — see ThetaUnderlyingTradeStream's own docstring for
        # why) — unlike the options streams below, this doesn't depend
        # on near-the-money chain discovery succeeding (a stock/index's
        # own price stream needs nothing more than its root symbol).
        for underlying in ACTIVE_UNDERLYINGS_BY_SYMBOL.values():
            if underlying.kind == UnderlyingKind.FUTURE:
                continue
            self._underlying_trade_stream.register_symbol(underlying.symbol, underlying.kind)

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
                self._stream.register_contract(
                    occ_symbol, root, chain.expiration, contract_type, strike
                )
                self._quote_stream.register_contract(
                    occ_symbol, root, chain.expiration, contract_type, strike
                )
        self._stream.start()
        self._quote_stream.start()
        self._underlying_trade_stream.start()

    async def stop(self) -> None:
        await self._stream.stop()
        await self._quote_stream.stop()
        await self._underlying_trade_stream.stop()
        self._client.close()

    def _get_json(self, path: str, **params: object) -> dict[str, Any]:
        # The one chokepoint every REST call passes through — bounding
        # it here covers get_option_chain, get_underlying_snapshot,
        # get_daily_bars, and the open-interest/rate lookups uniformly,
        # without touching each of them individually.
        with self._request_semaphore:
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
        with self._request_semaphore:
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
            today = datetime.now(EASTERN_TIME).date()
            unexpired_entries = [
                entry
                for entry in entries
                if date.fromisoformat(entry["contract"]["expiration"]) >= today
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
            open_interest = open_interest_by_key.get((root, strike, right), 0)
            volume = self._stream.cumulative_volume(occ_symbol)
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
        return bars

    async def stream_trades(self, underlying: str) -> AsyncIterator[FlowEvent]:
        queue = self._stream.subscribe_queue(underlying)
        while True:
            yield await queue.get()

    async def stream_quotes(self, underlying: str) -> AsyncIterator[QuoteEvent]:
        queue = self._quote_stream.subscribe_queue(underlying)
        while True:
            yield await queue.get()

    async def stream_underlying_trades(self, underlying: str) -> AsyncIterator[UnderlyingTradeEvent]:
        queue = self._underlying_trade_stream.subscribe_queue(underlying)
        while True:
            yield await queue.get()
