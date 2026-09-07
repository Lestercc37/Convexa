from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from backend.domain.entities import ContractType, FlowEvent, LatestQuote, OptionChain, Side
from backend.domain.ports import IStorage
from backend.domain.use_cases.calculate_bvc import (
    calculate_bvc_split,
    calculate_price_volatility,
)
from backend.domain.use_cases.calculate_lee_ready import classify_trade_side
from backend.domain.use_cases.market_hours import EASTERN_TIME


class WhaleAlertType(StrEnum):
    UNUSUAL = "UNUSUAL"
    WHALE = "WHALE"
    SUSTAINED_FLOW = "SUSTAINED_FLOW"


@dataclass(frozen=True, slots=True)
class WhaleAlertThresholds:
    unusual_min: Decimal = Decimal("40000.0")
    whale_min: Decimal = Decimal("150000.0")
    unusual_multiplier: Decimal = Decimal("3.0")
    whale_multiplier: Decimal = Decimal("6.0")
    # Initial calibration, not final — same "needs recalibration with real
    # data" caveat docs/use-cases.md already documents for the other four.
    # 15 minutes of accumulated flow at ~3.3x whale_min: sustained flow is
    # meant to require materially more cumulative dollars than a single
    # whale spike, not just one big period.
    sustained_flow_min: Decimal = Decimal("500000.0")


@dataclass(frozen=True, slots=True)
class WhaleAlert:
    symbol: str
    occ_symbol: str
    alert_type: WhaleAlertType
    amount: Decimal
    as_of: datetime
    # Bulk Volume Classification (Easley, López de Prado, O'Hara 2012)
    # estimates derived from price movement alone — never a measurement of
    # confirmed buy/sell-side order flow. See calculate_bvc.py.
    estimated_buy_volume: Decimal
    estimated_sell_volume: Decimal


@dataclass(frozen=True, slots=True)
class SymbolFlowPressure:
    """Net CLIENT (aggressor) options premium flow for one underlying,
    classified by Lee-Ready (process_trade) -- never a confirmed reading
    of dealer positioning. A trade's aggressor side tells us whether the
    *customer* bought or sold that contract; whether the dealer on the
    other side is opening or closing a position, and therefore what this
    implies about dealer gamma, is not observable from this alone. Field
    names say "client", not "dealer", deliberately, so this isn't
    mistaken for the latter downstream.

    Deliberately fed only by process_trade() (Lee-Ready, the real trade
    stream), never by process() (BVC, periodic chain-snapshot polling)
    even though both eventually classify buy/sell volume -- confirmed
    live, 2026-09: OptionContract.volume (what process()'s volume-delta
    BVC classification is derived from) already comes from
    ThetaTradeStream.cumulative_volume, the *same* trade-stream counter
    process_trade() classifies directly and more precisely, trade by
    trade rather than via a coarser volume-delta proxy. Accumulating
    both here would double-count the same real trades under two
    different classifiers. If the real trade stream isn't connected
    (MockDataProvider, or a real one that never connects), this stays
    empty rather than silently substituting the coarser BVC reading --
    the same "empty and honest, not a stand-in" convention this codebase
    already applies elsewhere (e.g. DerivedMetricValue.provisional).
    """

    symbol: str
    as_of: datetime
    net_call_premium: Decimal
    net_put_premium: Decimal
    net_client_flow_pressure: Decimal
    rolling_net_call_premium: Decimal
    rolling_net_put_premium: Decimal
    rolling_net_client_flow_pressure: Decimal
    rolling_window_minutes: int


def _contract_type_from_occ_symbol(occ_symbol: str) -> ContractType:
    """OCC symbols this codebase builds (_build_occ_symbol in the
    ThetaData adapter) always end in <YYMMDD><C or P><8-digit strike> --
    15 trailing characters regardless of root length, so the call/put
    character is always exactly 9 characters from the end."""
    return ContractType.CALL if occ_symbol[-9] == "C" else ContractType.PUT


def _floor_to_minute(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)


@dataclass(slots=True)
class _ContractState:
    cumulative_volume: int
    bucket_start: datetime
    bucket_amount: Decimal = Decimal(0)
    previous_amounts: deque[Decimal] = field(default_factory=lambda: deque(maxlen=5))
    sustained_amounts: deque[Decimal] = field(default_factory=lambda: deque(maxlen=15))
    sustained_alerted: bool = False
    # BVC: price history and the rolling volatility window are tracked
    # per raw reading (not per finalized minute) — each reading gets its
    # own buy/sell classification, accumulated into the bucket alongside
    # bucket_amount, same as the sustained-flow windows mirror the
    # whale/unusual one.
    #
    # Windowed by elapsed time (last `_PRICE_VOLATILITY_WINDOW`), not by
    # reading count — a fixed reading-count window (the original design)
    # implicitly assumed a roughly steady polling cadence: 20 readings at
    # ~30s each is ~10 real minutes, a reasonable volatility sample. That
    # assumption breaks once readings can arrive at irregular intervals
    # (a push/streaming provider, or simply a faster/slower poll cadence)
    # — 20 readings could then span 2 seconds during a burst or 20 minutes
    # during a lull, changing what sigma actually measures without the
    # code noticing. Anchoring to real elapsed time instead keeps the
    # window's meaning constant regardless of how often readings arrive —
    # same reasoning already applied to `previous_amounts`/
    # `sustained_amounts` below, which are anchored to finalized calendar
    # minutes rather than a raw reading count.
    previous_price: Decimal | None = None
    price_deltas: deque[tuple[datetime, Decimal]] = field(default_factory=deque)
    bucket_buy_volume: Decimal = Decimal(0)
    bucket_sell_volume: Decimal = Decimal(0)
    sustained_buy_volumes: deque[Decimal] = field(default_factory=lambda: deque(maxlen=15))
    sustained_sell_volumes: deque[Decimal] = field(default_factory=lambda: deque(maxlen=15))
    # Lee-Ready only (process_trade, tracked in a separate _trade_states
    # dict — see WhaleAlertsEngine.__init__) — the tick rule's "carry
    # forward on a zero tick" memory. process()/BVC never reads or writes
    # this field, so this addition changes nothing about that path.
    previous_side: Side = Side.UNKNOWN


@dataclass(slots=True)
class _SymbolFlowState:
    """Per-underlying (not per-contract) net client flow — see
    SymbolFlowPressure for the full methodology caveat. `rolling_flow`
    holds one (as_of, call_net, put_net) entry per process_trade() call
    for this symbol, trimmed to the trailing window as new entries
    arrive; `rolling_call_sum`/`rolling_put_sum` are maintained
    incrementally alongside it (updated on both append and trim) so
    reading the current rolling totals is O(1), not a fresh sum over the
    deque on every read."""

    session_date: date | None = None
    net_call_premium: Decimal = Decimal(0)
    net_put_premium: Decimal = Decimal(0)
    rolling_flow: deque[tuple[datetime, Decimal, Decimal]] = field(default_factory=deque)
    rolling_call_sum: Decimal = Decimal(0)
    rolling_put_sum: Decimal = Decimal(0)
    last_as_of: datetime | None = None


class WhaleAlertsEngine:
    """Detect unusual contract volume from provider-independent chain snapshots.

    Each call to `process()` carries one raw reading (today, roughly every
    30s, whenever the internal trigger fires — the engine itself has no
    fixed cadence). Readings are grouped into real calendar-minute buckets
    using `chain.as_of` (same floor-to-minute approach the frontend already
    uses for candles), and only a *finalized* 1-minute bucket is ever
    classified or windowed — never a raw sub-minute reading.

    Thresholds are read from `storage` fresh on every `process()` call,
    not cached at construction — so an edit made through the thresholds
    endpoint takes effect on the very next trigger, without a restart.
    Only the thresholds are re-read live; `_states`/`_alerts` (the
    per-contract windowing memory and alert history) stay exactly as
    long-lived, in-memory engine state — re-fetching those per call would
    defeat the whole windowing mechanism.
    """

    _WINDOW_SIZE = 5
    _SUSTAINED_WINDOW_SIZE = 15
    _CONTRACT_MULTIPLIER = Decimal(100)
    # 10 minutes: same order of magnitude the old 20-reading window
    # represented at the ~30s polling cadence it was designed around, but
    # anchored to real elapsed time so it holds regardless of how often
    # readings actually arrive (see `_ContractState.price_deltas`).
    _PRICE_VOLATILITY_WINDOW = timedelta(minutes=10)
    # Net client flow pressure (SymbolFlowPressure): same order of
    # magnitude as _PRICE_VOLATILITY_WINDOW (10min) and the per-contract
    # Sustained Flow window (15 readings) above -- picked to sit in the
    # same ballpark as this file's other time-based windows, not a new,
    # unrelated magnitude. Answers "is something concentrated happening
    # right now", alongside net_call_premium/net_put_premium's
    # session-since-open total, which answers "what's today's bias so
    # far" -- genuinely different questions, so both are kept rather than
    # picking one.
    _NET_FLOW_ROLLING_WINDOW = timedelta(minutes=15)

    def __init__(
        self,
        storage: IStorage,
        default_thresholds: WhaleAlertThresholds | None = None,
        alert_limit: int = 1000,
    ) -> None:
        self._storage = storage
        self._default_thresholds = default_thresholds or WhaleAlertThresholds()
        self._states: dict[str, _ContractState] = {}
        # Separate from _states (process()/BVC) on purpose — process_trade()
        # (Lee-Ready) keeps its own per-contract bucketing state so the two
        # mechanisms can run concurrently under ThetaDataProvider (which
        # still polls for greeks/IV/OI while also streaming trades) without
        # either one's volume ever being double-counted into the other's
        # bucket. See _ContractState and process_trade().
        self._trade_states: dict[str, _ContractState] = {}
        self._alerts: deque[WhaleAlert] = deque(maxlen=alert_limit)
        # Per-underlying (not per-contract) net client flow -- see
        # SymbolFlowPressure/_SymbolFlowState. Fed only by process_trade()
        # (see that method's own comment for why not process() too).
        self._symbol_flow: dict[str, _SymbolFlowState] = {}

    def _resolve_thresholds(self, symbol: str) -> WhaleAlertThresholds:
        persisted = self._storage.get_whale_thresholds().get(symbol.upper())
        if persisted is None:
            return self._default_thresholds
        return WhaleAlertThresholds(
            unusual_min=persisted.unusual_min,
            whale_min=persisted.whale_min,
            unusual_multiplier=persisted.unusual_multiplier,
            whale_multiplier=persisted.whale_multiplier,
            sustained_flow_min=persisted.sustained_flow_min,
        )

    def process(self, chain: OptionChain) -> tuple[WhaleAlert, ...]:
        generated: list[WhaleAlert] = []
        thresholds = self._resolve_thresholds(chain.symbol)
        current_bucket_start = _floor_to_minute(chain.as_of)

        for contract in chain.contracts:
            state = self._states.get(contract.occ_symbol)
            if state is None:
                self._states[contract.occ_symbol] = _ContractState(
                    cumulative_volume=contract.volume,
                    bucket_start=current_bucket_start,
                    previous_price=contract.last,
                )
                continue

            delta = contract.volume - state.cumulative_volume
            state.cumulative_volume = contract.volume
            if delta < 0:
                # Session rollover — the volume counter is no longer
                # comparable to anything accumulated so far, so every
                # window (whale/unusual, sustained flow, BVC price
                # history, the in-progress bucket) is discarded, same
                # treatment the original code already gave
                # `previous_amounts`.
                state.previous_amounts.clear()
                state.sustained_amounts.clear()
                state.sustained_alerted = False
                state.bucket_amount = Decimal(0)
                state.bucket_start = current_bucket_start
                state.previous_price = contract.last
                state.price_deltas.clear()
                state.bucket_buy_volume = Decimal(0)
                state.bucket_sell_volume = Decimal(0)
                state.sustained_buy_volumes.clear()
                state.sustained_sell_volumes.clear()
                continue

            amount = Decimal(delta) * contract.last * self._CONTRACT_MULTIPLIER

            # BVC: classified per raw reading (not per finalized minute),
            # using the reading's own price change against the current
            # rolling volatility window, then accumulated into the
            # in-progress bucket alongside `amount` — see calculate_bvc.py.
            price_delta = contract.last - state.previous_price
            state.price_deltas.append((chain.as_of, price_delta))
            cutoff = chain.as_of - self._PRICE_VOLATILITY_WINDOW
            while state.price_deltas and state.price_deltas[0][0] < cutoff:
                state.price_deltas.popleft()
            sigma = calculate_price_volatility([delta for _, delta in state.price_deltas])
            buy_volume, sell_volume = calculate_bvc_split(price_delta, sigma, Decimal(delta))
            state.previous_price = contract.last

            if current_bucket_start != state.bucket_start:
                generated.extend(
                    self._finalize_bucket(
                        state,
                        chain.symbol,
                        contract.occ_symbol,
                        chain.as_of,
                        thresholds,
                        current_bucket_start,
                    )
                )

            state.bucket_amount += amount
            state.bucket_buy_volume += buy_volume
            state.bucket_sell_volume += sell_volume

        return tuple(generated)

    def process_trade(self, event: FlowEvent, quote: LatestQuote | None) -> tuple[WhaleAlert, ...]:
        """Classify one individual trade with Lee-Ready (1991) and feed it
        into the same per-minute bucketing / Sustained Flow mechanism
        process() already uses — see calculate_lee_ready.py for the
        classification itself, and _ContractState/__init__ for why this
        keeps its own separate state (_trade_states, not _states).

        `quote` is whatever the caller (StreamWhaleAlertsUseCase) last saw
        on the Quote Stream for this contract — `None` means no quote has
        arrived yet (e.g. right at startup), which classify_trade_side
        already treats as a documented neutral case, not a guess.

        Known limitation, deliberately out of scope here: unlike process(),
        this has no volume-counter-based session-rollover detection (a
        continuous trade stream has no equivalent counter to watch) — the
        5-minute/15-minute rolling windows are not explicitly cleared
        across a session boundary. Swapping the classification mechanism
        was this change's only goal; rollover handling for the streaming
        path is a separate concern to revisit later.
        """
        thresholds = self._resolve_thresholds(event.symbol)
        current_bucket_start = _floor_to_minute(event.as_of)

        state = self._trade_states.get(event.occ_symbol)
        if state is None:
            state = _ContractState(cumulative_volume=0, bucket_start=current_bucket_start)
            self._trade_states[event.occ_symbol] = state

        # FlowEvent carries `premium` (price × size × 100), not the raw
        # per-contract trade price the quote rule/tick rule need directly.
        # Recovered by undoing that exact multiplication — exact under
        # Decimal, no rounding introduced — rather than adding a
        # price-specific field to FlowEvent, which is also reconstructed
        # from a persisted schema with no such column (postgresql.py).
        price = event.premium / (Decimal(event.size) * self._CONTRACT_MULTIPLIER)
        bid = quote.bid if quote is not None else None
        ask = quote.ask if quote is not None else None
        side = classify_trade_side(price, bid, ask, state.previous_price, state.previous_side)
        state.previous_price = price
        state.previous_side = side

        if side is Side.BUY:
            buy_volume, sell_volume = event.premium, Decimal(0)
        elif side is Side.SELL:
            buy_volume, sell_volume = Decimal(0), event.premium
        else:
            half = event.premium / 2
            buy_volume, sell_volume = half, half

        self._record_symbol_flow(
            event.symbol,
            event.as_of,
            _contract_type_from_occ_symbol(event.occ_symbol),
            buy_volume - sell_volume,
        )

        generated: list[WhaleAlert] = []
        if current_bucket_start != state.bucket_start:
            generated.extend(
                self._finalize_bucket(
                    state,
                    event.symbol,
                    event.occ_symbol,
                    event.as_of,
                    thresholds,
                    current_bucket_start,
                )
            )

        state.bucket_amount += event.premium
        state.bucket_buy_volume += buy_volume
        state.bucket_sell_volume += sell_volume
        return tuple(generated)

    def _finalize_bucket(
        self,
        state: _ContractState,
        symbol: str,
        occ_symbol: str,
        as_of: datetime,
        thresholds: WhaleAlertThresholds,
        new_bucket_start: datetime,
    ) -> list[WhaleAlert]:
        """Close `state`'s in-progress bucket: classify it against the
        trailing 5-minute average, roll it into the 15-minute Sustained
        Flow window, then reset the bucket for `new_bucket_start`. Shared
        by process() and process_trade() — identical bucketing/alerting
        rules regardless of which classifier (BVC or Lee-Ready) produced
        the bucket's buy/sell split.
        """
        generated: list[WhaleAlert] = []
        finalized_amount = state.bucket_amount
        finalized_buy_volume = state.bucket_buy_volume
        finalized_sell_volume = state.bucket_sell_volume

        if len(state.previous_amounts) == self._WINDOW_SIZE:
            average_amount = sum(state.previous_amounts, Decimal()) / self._WINDOW_SIZE
            alert_type = self._classify(finalized_amount, average_amount, thresholds)
            if alert_type is not None:
                generated.append(
                    self._emit(
                        symbol,
                        occ_symbol,
                        as_of,
                        alert_type,
                        finalized_amount,
                        finalized_buy_volume,
                        finalized_sell_volume,
                    )
                )

        state.sustained_amounts.append(finalized_amount)
        state.sustained_buy_volumes.append(finalized_buy_volume)
        state.sustained_sell_volumes.append(finalized_sell_volume)
        if len(state.sustained_amounts) == self._SUSTAINED_WINDOW_SIZE:
            sustained_total = sum(state.sustained_amounts, Decimal())
            if sustained_total >= thresholds.sustained_flow_min:
                if not state.sustained_alerted:
                    state.sustained_alerted = True
                    generated.append(
                        self._emit(
                            symbol,
                            occ_symbol,
                            as_of,
                            WhaleAlertType.SUSTAINED_FLOW,
                            sustained_total,
                            sum(state.sustained_buy_volumes, Decimal()),
                            sum(state.sustained_sell_volumes, Decimal()),
                        )
                    )
            else:
                state.sustained_alerted = False

        state.previous_amounts.append(finalized_amount)
        state.bucket_start = new_bucket_start
        state.bucket_amount = Decimal(0)
        state.bucket_buy_volume = Decimal(0)
        state.bucket_sell_volume = Decimal(0)
        return generated

    def _emit(
        self,
        symbol: str,
        occ_symbol: str,
        as_of: datetime,
        alert_type: WhaleAlertType,
        amount: Decimal,
        estimated_buy_volume: Decimal,
        estimated_sell_volume: Decimal,
    ) -> WhaleAlert:
        alert = WhaleAlert(
            symbol=symbol,
            occ_symbol=occ_symbol,
            alert_type=alert_type,
            amount=amount,
            as_of=as_of,
            estimated_buy_volume=estimated_buy_volume,
            estimated_sell_volume=estimated_sell_volume,
        )
        self._alerts.append(alert)
        # Dual-write, this phase only: whale_alerts now persists the same
        # alert this in-memory deque already held (see IStorage's own
        # docstring) -- /alerts and the screener still read _alerts, not
        # Postgres, until that migration is separately approved. Both
        # paths funnel through this one method (process() and
        # process_trade() both call _finalize_bucket, which only ever
        # calls _emit here), so this is the single place that needs it.
        self._storage.save_whale_alert(alert)
        return alert

    def recent_alerts(self, symbol: str, limit: int = 100) -> tuple[WhaleAlert, ...]:
        normalized = symbol.upper()
        matches = (alert for alert in reversed(self._alerts) if alert.symbol == normalized)
        return tuple(alert for _, alert in zip(range(limit), matches, strict=False))

    def _record_symbol_flow(
        self, symbol: str, as_of: datetime, contract_type: ContractType, net: Decimal
    ) -> None:
        normalized = symbol.upper()
        state = self._symbol_flow.setdefault(normalized, _SymbolFlowState())

        session_date = as_of.astimezone(EASTERN_TIME).date()
        if state.session_date != session_date:
            # New session (or the first reading ever for this symbol) --
            # the whole point of a session-accumulated total is that it
            # resets at the open, same convention as
            # calculate_session_open/isWithinRegularSession elsewhere in
            # this codebase, just enforced here rather than assumed from
            # a gap in incoming events (a Worker restart mid-session
            # would otherwise silently look like a fresh session too --
            # accepted, since a restart already loses this in-memory
            # state entirely regardless).
            state.session_date = session_date
            state.net_call_premium = Decimal(0)
            state.net_put_premium = Decimal(0)
            state.rolling_flow.clear()
            state.rolling_call_sum = Decimal(0)
            state.rolling_put_sum = Decimal(0)

        if contract_type == ContractType.CALL:
            state.net_call_premium += net
            state.rolling_call_sum += net
            state.rolling_flow.append((as_of, net, Decimal(0)))
        else:
            state.net_put_premium += net
            state.rolling_put_sum += net
            state.rolling_flow.append((as_of, Decimal(0), net))

        cutoff = as_of - self._NET_FLOW_ROLLING_WINDOW
        while state.rolling_flow and state.rolling_flow[0][0] < cutoff:
            _, old_call, old_put = state.rolling_flow.popleft()
            state.rolling_call_sum -= old_call
            state.rolling_put_sum -= old_put

        state.last_as_of = as_of

    def symbol_flow(self, symbol: str) -> SymbolFlowPressure | None:
        """Current net client flow pressure for `symbol`, or `None` if no
        trade has been classified for it yet this session (Worker just
        started, or the real trade stream isn't connected -- see
        SymbolFlowPressure's own docstring for why this is left empty
        rather than approximated)."""
        state = self._symbol_flow.get(symbol.upper())
        if state is None or state.last_as_of is None:
            return None
        return SymbolFlowPressure(
            symbol=symbol.upper(),
            as_of=state.last_as_of,
            net_call_premium=state.net_call_premium,
            net_put_premium=state.net_put_premium,
            net_client_flow_pressure=state.net_call_premium - state.net_put_premium,
            rolling_net_call_premium=state.rolling_call_sum,
            rolling_net_put_premium=state.rolling_put_sum,
            rolling_net_client_flow_pressure=state.rolling_call_sum - state.rolling_put_sum,
            rolling_window_minutes=int(self._NET_FLOW_ROLLING_WINDOW.total_seconds() // 60),
        )

    @staticmethod
    def _classify(
        amount: Decimal,
        average_amount: Decimal,
        thresholds: WhaleAlertThresholds,
    ) -> WhaleAlertType | None:
        if (
            amount >= thresholds.whale_min
            and amount > average_amount * thresholds.whale_multiplier
        ):
            return WhaleAlertType.WHALE
        if (
            amount >= thresholds.unusual_min
            and amount > average_amount * thresholds.unusual_multiplier
        ):
            return WhaleAlertType.UNUSUAL
        return None
