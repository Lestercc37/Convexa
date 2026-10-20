"""Standalone entrypoint for QUOTE/TRADE parsing and classification --
the CPU-bound work that used to run inline in backend/worker.py's own
ThetaStreamHub._process_messages, as a separate asyncio.Task on the same
event loop (PR #219, 2026-10-01).

Split out 2026-10-02 (v1, PR #226): PR #219 proved that separating the
WS read loop (_consume) from parsing/dispatch (_process_messages) into
two asyncio Tasks stopped the read loop from being directly blocked by
processing -- confirmed live, zero SLOW CONSUMER disconnects of that
specific kind since. It did NOT, and architecturally could not, give the
two genuine parallelism: both tasks still run on the same event loop,
same OS thread, same process, cooperatively interleaved at await points
only. Confirmed live, 2026-10-02 (message-lag diagnostic logging, added
the same day): _process_messages' own per-message work measured
100-150ms on individual iterations under real market-open volume
(~17,000-23,000 msgs/sec) -- during that whole synchronous span the
event loop cannot run _consume()'s own pending recv() either, enough to
build a backlog of 2,000-3,000+ messages at that rate. Matches Theta
Terminal's own independent "SLOW CONSUMER: N packets dropped" log for
the same incidents (1,062,092 dropped packets, 2026-10-02 9:50-11:55am
ET) and Eduardo/ThetaData support's own read of it: the GIL, not the
two-task split, was the real ceiling. Only a separate OS process -- its
own interpreter, its own GIL -- removes it.

v2 (2026-10-02), this file: v1 sent every classified result back to
worker.py over the relay and collapsed in ~2.3 minutes under real volume
-- see docs/stream-processor-split-postmortem-2026-10-02.md for the full
postmortem. The return flow doubled total IPC traffic to roughly the
same real ~20,000 msgs/sec rate each way, regardless of whether either
side technically "waited" for a reply; that's what exhausted a
20,000-item queue in 2.29 seconds. v2 removes the return flow entirely
and replicates the one pattern in this codebase already proven at this
exact volume, one-way: whale_alerts_relay.py (PR #179). This process now
reaches every real destination for a classified event DIRECTLY instead
of reporting back to worker.py:

- QUOTE/option TRADE -> WhaleAlertsRelayServer.publish_quote/publish_trade,
  a NEW instance of the exact same class worker.py used to own (moved,
  not duplicated -- see that class's own docstring in
  backend/core/whale_alerts_relay.py, completely unchanged). backend/
  whale_alerts_worker.py doesn't care which process answers on
  whale_alerts_relay_host:port, so this needed zero changes there.
- option TRADE size -> this process's own _cumulative_volume dict,
  exported to Postgres every EXPORT_INTERVAL_SECONDS (same cadence and
  same storage.save_cumulative_volumes call StreamStateExporter already
  uses, just owned here now since this is where the volume is actually
  tracked when a processor is connected).
- underlying TRADE -> StreamUnderlyingPriceUseCase.persist_if_due, the
  exact same market-hours-gated, per-symbol-debounced logic
  UnderlyingPriceStreamManager already uses in worker.py -- writes
  through AsyncPostgreSQLStorage.save_market_price, which NOTIFYs
  MARKET_PRICE_CHANNEL inside the same transaction (see
  core/price_notifications.py), so the chart's live WebSocket push picks
  it up exactly as it does today, regardless of which process wrote it.

This process never opens a connection to ThetaData itself -- Theta
Terminal allows exactly one WebSocket connection account-wide (see
ThetaStreamHub's own docstring), already held by backend/worker.py.
Instead it connects to that process's local-only relay
(StreamProcessorRelayServer, backend/core/stream_processor_relay.py)
over a plain TCP socket on this same machine, one-way: receives raw WS
frames, parses/classifies them with the exact same pure logic
ThetaStreamHub._handle_quote/_handle_option_trade/
_handle_underlying_trade call when running in-process (see
backend/adapters/providers/thetadata/stream_parsing.py -- literally the
same functions, not a reimplementation). Nothing is ever sent back over
that connection.

Deliberately stateless beyond ACTIVE_UNDERLYINGS (a static, shared
constant both processes import independently -- see
parse_underlying_trade_message's own `symbols` parameter) and its own
_cumulative_volume accumulator: no storage engine beyond what
build_container() already wires for the whale-alerts-relay/price-write
paths above.

If this process is down: worker.py's own StreamProcessorRelayServer.
publish_raw() just returns False (no client connected) and
_process_messages falls back to handling QUOTE/TRADE itself, in-process
-- today's known-working (if GIL-contended) behavior, market data never
silently dropped. In that fallback mode, worker.py's own (unchanged)
UnderlyingPriceStreamManager/WhaleAlertsRelayServer/StreamStateExporter
keep working exactly as before this whole split existed, reading from
ThetaStreamHub's own subscriber queues -- so Whale Alerts and the
tick-level price boost pause only while THIS process specifically is
down, the same acceptable-degradation story this codebase already
accepts for whale_alerts_worker.py's own downtime (see that module's
own docstring), now with one more link in the same chain. Core market
data (the REST scheduler's 30s cadence) is never affected either way --
the one invariant none of this is allowed to touch.

Run with:
    python -m backend.stream_processor_worker

Also run backend/worker.py (as its own separate process) -- without it,
this process has no relay server to connect to, and never receives a
single raw frame.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import time
from datetime import date, datetime, timedelta
from decimal import Decimal

from backend.adapters.providers.thetadata.stream_parsing import (
    parse_option_trade_message,
    parse_quote_message,
    parse_underlying_trade_message,
)
from backend.core import fastjson
from backend.core.container import build_container
from backend.core.logging import configure_logging
from backend.core.stream_processor_relay import StreamProcessorRelayClient
from backend.core.whale_alerts_relay import WhaleAlertsRelayServer
from backend.domain.entities import OptionQuoteMinute, QuoteEvent, UnderlyingTradeEvent
from backend.domain.underlyings import ACTIVE_UNDERLYINGS
from backend.domain.use_cases.large_prints import LargePrintRecorder
from backend.domain.use_cases.minute_flow import MINUTE_FLOW_SYMBOLS, MinuteFlowRecorder
from backend.domain.use_cases.market_hours import EASTERN_TIME, is_market_open
from backend.domain.use_cases.stream_underlying_price import (
    StreamUnderlyingPriceUseCase,
    sets_last_price,
)
from backend.domain.use_cases.trade_date import (
    next_trade_date_roll,
    trade_date_of,
    volume_rows_since,
)

logger = logging.getLogger(__name__)

_SYMBOLS = {underlying.symbol: underlying.kind for underlying in ACTIVE_UNDERLYINGS}

# Same cadence as StreamStateExporter -- see that module's own comment
# for why 15s (frequent enough that the scheduler-only process's own
# ~60-75s cycle never reads anything more than one export interval
# stale, cheap enough not to matter at that cadence).
VOLUME_EXPORT_INTERVAL_SECONDS = 15

# Price writes in flight at once, across all symbols; well under the
# engine's pool (5 + 10 overflow) so the volume export and anything else
# always find a free connection.
PRICE_WRITE_CONCURRENCY = 4

# A repeated bid/ask for a contract is forwarded again after this long, see
# _ProcessorState.is_repeated_quote.
QUOTE_REFRESH_SECONDS = 1.0

FRAME_MIX_LOG_INTERVAL_SECONDS = 60

# option_quote_minutes (migration 0040): the flush runs this long after each minute boundary so the last quotes of
# the minute are in; one INFO line with the rows written every 15 minutes.
MINUTE_QUOTES_FLUSH_DELAY_SECONDS = 0.3
MINUTE_QUOTES_LOG_EVERY_SECONDS = 900

# large_trade_prints (migration 0043): captured in memory, flushed in one batch this often (a few hundred rows a day for SPY+QQQ).
LARGE_PRINTS_FLUSH_SECONDS = 30
LARGE_PRINTS_LOG_EVERY_SECONDS = 1800

# minute_option_flow (migration 0048): the flush runs this long after each minute boundary (late trades of the minute are still accepted and ADDED to the row), and the |delta| of every contract is
# refreshed from the latest chain snapshots once a minute per underlying.
MINUTE_FLOW_FLUSH_DELAY_SECONDS = 3.0
MINUTE_FLOW_DELTA_REFRESH_SECONDS = 60
MINUTE_FLOW_LOG_EVERY_SECONDS = 1800


class _ProcessorState:
    """Everything one raw frame's handling needs beyond the pure
    parse_*_message functions themselves -- bundled so
    _handle_raw_frame can stay a plain function (easy to unit test)
    instead of a bound method on a larger class."""

    def __init__(
        self,
        whale_alerts_relay: WhaleAlertsRelayServer,
        price_use_case: StreamUnderlyingPriceUseCase,
    ) -> None:
        self.whale_alerts_relay = whale_alerts_relay
        self.price_use_case = price_use_case
        self.cumulative_volume: dict[str, int] = {}
        # Trade date (OPRA convention: opens 20:15 ET of the previous calendar day, see
        # use_cases/trade_date.py) the counters belong to; see roll_volume_day.
        self.volume_date: date | None = None
        # time.time() of the next 20:15 ET boundary; 0.0 = not computed yet (roll_if_due).
        self._next_roll_epoch: float = 0.0
        # What this process is spending its time on: frames per (type, root)
        # since the last report, see _log_frame_mix_periodically.
        self.frame_mix: collections.Counter[tuple[str, object]] = collections.Counter()
        self.repeated_quotes = 0
        self._last_quote: dict[tuple, tuple[object, object, float]] = {}
        # Last changed bid/ask per contract since the last minute flush, and the last values written, see
        # record_minute_quote / take_minute_rows (table option_quote_minutes, migration 0040).
        self.minute_quotes_enabled = True
        self.minute_quotes: dict[str, tuple[Decimal, Decimal, datetime]] = {}
        self._minute_quotes_last_written: dict[str, tuple[Decimal, Decimal]] = {}
        self._minute_quotes_date: date | None = None
        self.large_prints_enabled = True
        self.large_prints = LargePrintRecorder()
        self.minute_flow_enabled = False
        self.minute_flow = MinuteFlowRecorder()
        self._pending_prices: dict[str, UnderlyingTradeEvent] = {}
        self._price_writers: dict[str, asyncio.Task[None]] = {}
        self._price_write_slots = asyncio.Semaphore(PRICE_WRITE_CONCURRENCY)

    def roll_if_due(self, now: datetime | None = None) -> None:
        """Rolls the counters the moment the trade date changes (20:15 ET), checked on every option
        TRADE: a plain float compare against the next boundary, so a roll costs nothing per frame and
        the first prints of the evening session land in the NEW day's counters (a periodic check every
        15 s would have thrown them away). `now` is only for tests."""
        epoch = now.timestamp() if now is not None else time.time()
        if epoch < self._next_roll_epoch:
            return
        current = now if now is not None else datetime.now(EASTERN_TIME)
        self.roll_volume_day(trade_date_of(current))
        self._next_roll_epoch = next_trade_date_roll(current).timestamp()

    def roll_volume_day(self, today: date) -> bool:
        """Clears the volume counters when the TRADE DATE changes (opens 20:15 ET, was 00:00 ET until
        2026-10-13: that dropped the previous evening's session, the fixed per-contract shortfall
        against thinkorswim). Cumulative volume is a per-trade-date figure but nothing ever reset
        it: the counters only restarted when the process did, so a contract trading on several days
        kept adding to the previous days' total (2026-10-06: QQQ261006P00749000 stored 86,030 vs
        7,745 on ThetaData). `today` is the TRADE DATE (see use_cases/trade_date.py)."""
        if self.volume_date is None:
            self.volume_date = today
            return False
        if today == self.volume_date:
            return False
        logger.info(
            "Stream processor worker: new session date %s, resetting the cumulative volume of %d contracts",
            today,
            len(self.cumulative_volume),
        )
        self.cumulative_volume.clear()
        self.volume_date = today
        return True

    def record_minute_quote(self, event: QuoteEvent) -> None:
        """Keeps the latest bid/ask per contract for the current minute: one dict assignment per quote that
        is not a repeat (is_repeated_quote already dropped the ~85% that only changed sizes)."""
        if self.minute_quotes_enabled:
            self.minute_quotes[event.occ_symbol] = (event.bid, event.ask, event.as_of)

    def take_minute_rows(self, minute_start: datetime) -> list[OptionQuoteMinute]:
        """Hands over the minute that just ended and starts a new one. Same shape as ThetaData's quote flat
        file: a contract whose bid/ask equals what was last written has no row (carry the previous one
        forward), so a quiet contract costs nothing. The last-written memory restarts every session day.
        Nothing is marked as written here: see mark_minute_rows_written."""
        quotes, self.minute_quotes = self.minute_quotes, {}
        if self._minute_quotes_date != minute_start.date():
            self._minute_quotes_date = minute_start.date()
            self._minute_quotes_last_written.clear()
        return [
            OptionQuoteMinute(minute_start, occ_symbol, bid, ask, quote_time)
            for occ_symbol, (bid, ask, quote_time) in quotes.items()
            if self._minute_quotes_last_written.get(occ_symbol) != (bid, ask)
        ]

    def mark_minute_rows_written(self, rows: list[OptionQuoteMinute]) -> None:
        for row in rows:
            self._minute_quotes_last_written[row.occ_symbol] = (row.bid, row.ask)

    def is_repeated_quote(self, message: dict) -> bool:
        """True when this QUOTE carries the same bid/ask as the last one
        forwarded for the contract (a size-only update) and that one went out
        less than QUOTE_REFRESH_SECONDS ago. The whale engine only needs the
        latest bid/ask when a trade arrives, so a repeat adds nothing, but
        parsing and relaying every one of them kept this process at 99% CPU
        at the 2026-10-05 open (_build_occ_symbol, json, relay encode) and
        its relay queue overflowed. The periodic refresh keeps the forwarded
        quote's as_of from ever looking old."""
        contract = message.get("contract", {})
        quote = message.get("quote", {})
        key = (contract.get("root"), contract.get("expiration"), contract.get("strike"), contract.get("right"))
        bid = quote.get("bid")
        ask = quote.get("ask")
        now = time.monotonic()
        last = self._last_quote.get(key)
        if last is not None and last[0] == bid and last[1] == ask and now - last[2] < QUOTE_REFRESH_SECONDS:
            return True
        self._last_quote[key] = (bid, ask, now)
        return False

    def schedule_price_write(self, event: UnderlyingTradeEvent) -> None:
        """Latest-wins, one writer per symbol. Tick-level symbols are written
        on every tick (see TICK_LEVEL_SYMBOLS), and a task per tick exhausted
        the Postgres pool (5+10) within a minute of the 2026-10-05 open: the
        tasks piled up waiting for a connection, the processor stopped
        consuming and the stored price froze. A write only ever needs the
        newest tick, so while one is in flight newer ticks just replace the
        pending one.

        A print that cannot set the last price (odd lot, average price, ...: see
        NON_LAST_SALE_CONDITIONS) is dropped here, not only inside persist_if_due: it must
        not replace a pending real tick, which would then never be written."""
        if not sets_last_price(event):
            return
        self._pending_prices[event.symbol] = event
        writer = self._price_writers.get(event.symbol)
        if writer is None or writer.done():
            self._price_writers[event.symbol] = asyncio.create_task(self._drain_price_writes(event.symbol))

    async def _drain_price_writes(self, symbol: str) -> None:
        while True:
            event = self._pending_prices.pop(symbol, None)
            if event is None:
                return
            try:
                async with self._price_write_slots:
                    await self.price_use_case.persist_if_due(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Stream processor worker: persisting the %s price failed", symbol)


def _handle_raw_frame(state: _ProcessorState, raw: str) -> None:
    """Parses one raw WS frame and publishes the result directly to
    wherever it's actually consumed -- see this module's own docstring.
    Synchronous and exception-isolated per frame: a single malformed
    message must never take down this process's whole relay connection,
    same principle as StreamProcessorRelayServer's own reader loop."""
    try:
        message = fastjson.loads(raw)
        header = message.get("header", {})
        msg_type = header.get("type")
        if msg_type in ("QUOTE", "TRADE"):
            state.frame_mix[(msg_type, message.get("contract", {}).get("root"))] += 1
        if msg_type == "QUOTE":
            if state.is_repeated_quote(message):
                state.repeated_quotes += 1
                return
            parsed = parse_quote_message(message)
            if parsed is not None:
                state.whale_alerts_relay.publish_quote(parsed.event)
                state.record_minute_quote(parsed.event)
                if state.minute_flow_enabled:
                    state.minute_flow.observe_quote(parsed.event)
        elif msg_type == "TRADE":
            security_type = message.get("contract", {}).get("security_type")
            if security_type == "OPTION":
                parsed = parse_option_trade_message(message)
                if parsed is not None:
                    state.roll_if_due()
                    state.cumulative_volume[parsed.occ_symbol] = (
                        state.cumulative_volume.get(parsed.occ_symbol, 0) + parsed.size
                    )
                    if parsed.event is not None:
                        state.whale_alerts_relay.publish_trade(parsed.event)
                        if state.minute_flow_enabled:
                            state.minute_flow.observe_trade(parsed.event, parsed.exchange_ts)
            elif security_type in ("STOCK", "INDEX"):
                parsed_underlying = parse_underlying_trade_message(message, _SYMBOLS)
                if parsed_underlying is not None:
                    # Fire-and-forget on purpose: persist_if_due does a
                    # real async Postgres read+write when due (most
                    # calls return instantly on the debounce check
                    # alone) -- awaiting it inline here would serialize
                    # raw-frame handling behind that I/O, exactly the
                    # kind of self-inflicted backpressure v1's postmortem
                    # flagged. asyncio.create_task keeps this frame's
                    # handling non-blocking regardless. Coalesced per
                    # symbol (ProcessorState.schedule_price_write) so a
                    # tick burst can't pile up unbounded writes.
                    state.schedule_price_write(parsed_underlying.event)
                    if state.large_prints_enabled:
                        state.large_prints.observe(parsed_underlying.event, parsed_underlying.exchange_ts)
    except Exception:
        logger.exception("Stream processor worker: failed to parse one raw frame, dropping it")


async def _resume_cumulative_volume(container, state: _ProcessorState) -> None:
    """Seeds the in-memory volume counters with what this process already
    exported earlier today, so a restart mid-session doesn't wipe the day's
    volume (the first export after a restart would otherwise overwrite the
    stored rows with only the counts since then). Never blocks startup."""
    try:
        now = datetime.now(EASTERN_TIME)
        state.volume_date = trade_date_of(now)
        # the current trade date opened at 20:15 ET of the previous calendar day (evening session included);
        # volume_rows_since also guards the one-time transition from the per-calendar-day counters
        stored = await asyncio.to_thread(container.storage.get_cumulative_volumes_since, volume_rows_since(now))
    except Exception:
        logger.exception(
            "Stream processor worker: could not resume today's cumulative volume, starting from zero"
        )
        return
    for occ_symbol, volume in stored.items():
        state.cumulative_volume[occ_symbol] = max(volume, state.cumulative_volume.get(occ_symbol, 0))
    logger.info(
        "Stream processor worker: resumed today's cumulative volume for %d contracts", len(stored)
    )


async def _record_option_quote_minutes_periodically(container, state: _ProcessorState) -> None:
    """At every minute boundary (ET) writes the last changed bid/ask of each contract of the minute that
    just ended to option_quote_minutes, regular session only. Never raises: a failed write loses that
    minute's rows (logged), it must not take the stream processor down."""
    rows_since_log = 0
    last_log = time.monotonic()
    while True:
        now = datetime.now(EASTERN_TIME)
        next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        await asyncio.sleep(max(0.5, (next_minute - now).total_seconds() + MINUTE_QUOTES_FLUSH_DELAY_SECONDS))
        minute_start = next_minute - timedelta(minutes=1)
        try:
            rows = state.take_minute_rows(minute_start)
            if not is_market_open(minute_start) or not rows:
                continue
            await container.async_market_storage.save_option_quote_minutes(rows)
            state.mark_minute_rows_written(rows)
            rows_since_log += len(rows)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Stream processor worker: writing the minute quotes failed, that minute is lost")
        if time.monotonic() - last_log >= MINUTE_QUOTES_LOG_EVERY_SECONDS:
            logger.info(
                "Stream processor worker: option_quote_minutes wrote %d rows in the last %.0f min (last minute %s)",
                rows_since_log,
                (time.monotonic() - last_log) / 60,
                minute_start.strftime("%H:%M"),
            )
            rows_since_log = 0
            last_log = time.monotonic()


async def _refresh_minute_flow_deltas_periodically(container, state: _ProcessorState) -> None:
    """Once a minute per underlying: the |delta| of every contract from its latest chain snapshots (one light query each, in a worker thread). A failed read keeps the deltas already held."""
    while True:
        for symbol in sorted(state.minute_flow.symbols):
            try:
                deltas = await asyncio.to_thread(container.storage.get_latest_option_abs_deltas, symbol)
                if deltas:
                    state.minute_flow.set_deltas(deltas)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Stream processor worker: reading the %s option deltas for the minute flow failed, keeping the last ones", symbol)
        await asyncio.sleep(MINUTE_FLOW_DELTA_REFRESH_SECONDS)


async def _flush_minute_flow_periodically(container, state: _ProcessorState) -> None:
    """At every minute boundary (+ a few seconds) writes the signed delta flow of the minutes that ended to minute_option_flow. Never raises: a failed write puts its minutes back for the
    next flush (bounded, see MinuteFlowRecorder), it must not take the stream processor down."""
    written = 0
    last_log = time.monotonic()
    while True:
        now = datetime.now(EASTERN_TIME)
        next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        await asyncio.sleep(max(0.5, (next_minute - now).total_seconds() + MINUTE_FLOW_FLUSH_DELAY_SECONDS))
        rows = state.minute_flow.take_before(time.time() - MINUTE_FLOW_FLUSH_DELAY_SECONDS + 1.0)
        if rows:
            try:
                await container.async_market_storage.save_minute_option_flow(rows)
                written += len(rows)
            except asyncio.CancelledError:
                state.minute_flow.put_back(rows)
                raise
            except Exception:
                state.minute_flow.put_back(rows)
                logger.exception("Stream processor worker: writing %d minute-flow rows failed, they stay queued", len(rows))
        if time.monotonic() - last_log >= MINUTE_FLOW_LOG_EVERY_SECONDS:
            logger.info(
                "Stream processor worker: minute_option_flow wrote %d rows in the last %.0f min",
                written,
                (time.monotonic() - last_log) / 60,
            )
            written = 0
            last_log = time.monotonic()


async def _flush_large_prints_periodically(container, state: _ProcessorState) -> None:
    """Writes the large SPY/QQQ prints captured since the last flush (large_trade_prints, migration 0043). Never raises: a failed write puts its
    rows back for the next flush (bounded, see LargePrintRecorder), it must not take the stream processor down."""
    written = 0
    last_log = time.monotonic()
    while True:
        await asyncio.sleep(LARGE_PRINTS_FLUSH_SECONDS)
        rows = state.large_prints.take()
        if rows:
            try:
                await container.async_market_storage.save_large_trade_prints(rows)
                written += len(rows)
            except asyncio.CancelledError:
                state.large_prints.put_back(rows)
                raise
            except Exception:
                state.large_prints.put_back(rows)
                logger.exception("Stream processor worker: writing %d large prints failed, they stay queued", len(rows))
        if time.monotonic() - last_log >= LARGE_PRINTS_LOG_EVERY_SECONDS:
            logger.info(
                "Stream processor worker: large_trade_prints wrote %d rows in the last %.0f min",
                written,
                (time.monotonic() - last_log) / 60,
            )
            written = 0
            last_log = time.monotonic()


async def _log_frame_mix_periodically(state: _ProcessorState) -> None:
    """One INFO line a minute: frames per second by type and the top roots, so
    "which symbols load the stream" is answered from the log, not guessed."""
    while True:
        await asyncio.sleep(FRAME_MIX_LOG_INTERVAL_SECONDS)
        mix, repeated = state.frame_mix, state.repeated_quotes
        state.frame_mix = collections.Counter()
        state.repeated_quotes = 0
        total = sum(mix.values())
        if not total:
            continue
        per_type = collections.Counter()
        for (msg_type, _root), count in mix.items():
            per_type[msg_type] += count
        by_root = collections.Counter()
        for (_msg_type, root), count in mix.items():
            by_root[root] += count
        top = ", ".join(
            f"{root}={count / FRAME_MIX_LOG_INTERVAL_SECONDS:.0f}/s ({count / total:.0%})"
            for root, count in by_root.most_common(8)
        )
        logger.info(
            "Stream processor frame mix: %.0f frames/s (QUOTE %.0f/s of which %.0f/s repeats, TRADE %.0f/s); top roots: %s",
            total / FRAME_MIX_LOG_INTERVAL_SECONDS,
            per_type["QUOTE"] / FRAME_MIX_LOG_INTERVAL_SECONDS,
            repeated / FRAME_MIX_LOG_INTERVAL_SECONDS,
            per_type["TRADE"] / FRAME_MIX_LOG_INTERVAL_SECONDS,
            top,
        )


async def _export_volume_periodically(container, state: _ProcessorState) -> None:
    """This process's own half of what StreamStateExporter already does
    for worker.py's in-process fallback path -- same cadence, same
    storage call, owned here because this is where volume is actually
    tracked whenever a processor is connected. See save_cumulative_
    volumes' own guard against an empty dict -- worker.py's own
    StreamStateExporter keeps running unconditionally too (harmless,
    its own _cumulative_volume just stays empty while this process is
    the one doing real classification, so it never has anything to
    export, let alone something that could overwrite this process's
    own, fresher data)."""
    while True:
        await asyncio.sleep(VOLUME_EXPORT_INTERVAL_SECONDS)
        try:
            state.roll_volume_day(trade_date_of(datetime.now(EASTERN_TIME)))
            if state.cumulative_volume:
                await asyncio.to_thread(
                    container.storage.save_cumulative_volumes, dict(state.cumulative_volume)
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Stream processor worker: cumulative volume export failed, will retry "
                "next interval"
            )


async def run() -> None:
    container = build_container()
    configure_logging(container.settings, log_file="logs/stream_processor_worker.log")
    logger.info("Starting %s stream processor worker", container.settings.app_name)

    if not container.settings.enable_scheduler:
        # Same kill switch backend/worker.py's own process checks.
        logger.warning(
            "enable_scheduler is False -- stream processor worker has nothing to "
            "start, exiting"
        )
        return

    whale_alerts_relay = WhaleAlertsRelayServer(
        container.settings.whale_alerts_relay_host, container.settings.whale_alerts_relay_port
    )
    await whale_alerts_relay.start()
    price_use_case = StreamUnderlyingPriceUseCase(
        provider=container.market_data_provider,  # never streamed from here, see this
        # module's own docstring -- persist_if_due is the only method
        # this process ever calls on it.
        storage=container.async_market_storage,
    )
    state = _ProcessorState(whale_alerts_relay, price_use_case)
    await _resume_cumulative_volume(container, state)
    export_task = asyncio.create_task(_export_volume_periodically(container, state))
    frame_mix_task = asyncio.create_task(_log_frame_mix_periodically(state))
    state.minute_quotes_enabled = container.settings.option_quote_minutes_enabled
    minute_quotes_task = (
        asyncio.create_task(_record_option_quote_minutes_periodically(container, state))
        if state.minute_quotes_enabled
        else None
    )

    state.large_prints_enabled = container.settings.large_trade_prints_enabled
    large_prints_task = (
        asyncio.create_task(_flush_large_prints_periodically(container, state)) if state.large_prints_enabled else None
    )

    state.minute_flow_enabled = container.settings.minute_option_flow_enabled
    if state.minute_flow_enabled:
        configured = frozenset(s.strip().upper() for s in container.settings.minute_option_flow_symbols.split(",") if s.strip())
        state.minute_flow = MinuteFlowRecorder(configured or MINUTE_FLOW_SYMBOLS)
    minute_flow_tasks = (
        [
            asyncio.create_task(_refresh_minute_flow_deltas_periodically(container, state)),
            asyncio.create_task(_flush_minute_flow_periodically(container, state)),
        ]
        if state.minute_flow_enabled
        else []
    )

    relay = StreamProcessorRelayClient(
        container.settings.stream_processor_relay_host,
        container.settings.stream_processor_relay_port,
    )
    logger.info(
        "Stream processor worker running, connecting to relay at %s:%s -- publishing "
        "Whale Alerts directly on %s:%s",
        container.settings.stream_processor_relay_host,
        container.settings.stream_processor_relay_port,
        container.settings.whale_alerts_relay_host,
        container.settings.whale_alerts_relay_port,
    )
    try:
        await relay.run(on_raw_frame=lambda raw: _handle_raw_frame(state, raw))
    finally:
        frame_mix_task.cancel()
        if minute_quotes_task is not None:
            minute_quotes_task.cancel()
        if large_prints_task is not None:
            large_prints_task.cancel()
        for task in minute_flow_tasks:
            task.cancel()
        export_task.cancel()
        try:
            await export_task
        except asyncio.CancelledError:
            pass
        await whale_alerts_relay.stop()
        if container.storage_engine is not None:
            container.storage_engine.dispose()
        if container.whale_alerts_storage_engine is not None:
            container.whale_alerts_storage_engine.dispose()
        await container.database_engine.dispose()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
