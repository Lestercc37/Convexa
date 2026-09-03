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
# - Stocks/Indices subscriptions (needed for the underlying's own share
#   volume, used by Anchored VWAP) were not active as of this adapter's
#   investigation — `MarketSnapshot.volume` is `0`, the same limitation
#   the FlashAlpha investigation ran into with its own equivalent gap.
#   `underlying_price` itself is NOT affected — it comes from the
#   options endpoint above regardless of the Stocks/Indices subscription
#   state.
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
                right = contract_meta["right"]
                contract_type = ContractType.CALL if right == "CALL" else ContractType.PUT
                strike = Decimal(str(contract_meta["strike"]))
                occ_symbol = _build_occ_symbol(symbol, chain.expiration, contract_type, strike)
                self._stream.register_contract(
                    occ_symbol, symbol, chain.expiration, contract_type, strike
                )
                self._quote_stream.register_contract(
                    occ_symbol, symbol, chain.expiration, contract_type, strike
                )
        self._stream.start()
        self._quote_stream.start()

    async def stop(self) -> None:
        await self._stream.stop()
        await self._quote_stream.stop()
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
        body = self._get_json(
            "/v3/option/snapshot/greeks/first_order",
            symbol=symbol,
            expiration=expiration_param,
            strike_range=NEAR_THE_MONEY_OVERFETCH_STRIKE_RANGE,
            format="json",
        )
        entries = [entry for entry in body.get("response", []) if entry.get("data")]
        if not entries:
            raise RuntimeError(f"ThetaData returned no near-the-money contracts for {symbol}")
        if expiration is not None:
            result = _NearTheMoneyChain(expiration, self._filter_near_the_money(symbol, entries))
        else:
            nearest = min(date.fromisoformat(entry["contract"]["expiration"]) for entry in entries)
            nearest_entries = [
                entry
                for entry in entries
                if date.fromisoformat(entry["contract"]["expiration"]) == nearest
            ]
            result = _NearTheMoneyChain(nearest, self._filter_near_the_money(symbol, nearest_entries))

        self._near_the_money_cache[cache_key] = (time.monotonic(), result)
        return result

    def _fetch_open_interest(
        self, symbol: str, expiration: date
    ) -> dict[tuple[Decimal, str], int]:
        cache_key = (symbol, expiration)
        cached = self._open_interest_cache.get(cache_key)
        if cached is not None and time.monotonic() - cached[0] < OPEN_INTEREST_CACHE_TTL_SECONDS:
            return cached[1]

        body = self._get_json(
            "/v3/option/snapshot/open_interest",
            symbol=symbol,
            expiration=expiration.strftime("%Y-%m-%d"),
            # Same over-fetch width as _fetch_near_the_money — every
            # contract that survives that method's price-width filter
            # must also have real open-interest data available here,
            # not a silent 0 fallback for strikes beyond the old narrow
            # range.
            strike_range=NEAR_THE_MONEY_OVERFETCH_STRIKE_RANGE,
            format="json",
        )
        result: dict[tuple[Decimal, str], int] = {}
        for entry in body.get("response", []):
            data_points = entry.get("data") or []
            if not data_points:
                continue
            contract_meta = entry["contract"]
            key = (Decimal(str(contract_meta["strike"])), contract_meta["right"])
            result[key] = int(data_points[0]["open_interest"])

        self._open_interest_cache[cache_key] = (time.monotonic(), result)
        return result

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

            occ_symbol = _build_occ_symbol(symbol, chain.expiration, contract_type, strike)
            open_interest = open_interest_by_key.get((strike, right), 0)
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
            key = (Decimal(str(contract_meta["strike"])), contract_meta["right"])
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
            # Stocks/Indices subscriptions not active as of this adapter's
            # investigation (confirmed live, both 403 FREE-tier) — no
            # source anywhere for the underlying's own share volume.
            # Anchored VWAP already handles this safely on its own
            # (provisional=True, value=None) — see calculate_anchored_vwap.
            volume=0,
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
