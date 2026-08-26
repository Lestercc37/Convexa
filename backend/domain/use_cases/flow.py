from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from backend.domain.entities import OptionChain, OptionContract
from backend.domain.ports import IStorage
from backend.domain.use_cases.calculate_bvc import (
    calculate_bvc_split,
    calculate_price_volatility,
)


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

    def __init__(
        self,
        storage: IStorage,
        default_thresholds: WhaleAlertThresholds | None = None,
        alert_limit: int = 1000,
    ) -> None:
        self._storage = storage
        self._default_thresholds = default_thresholds or WhaleAlertThresholds()
        self._states: dict[str, _ContractState] = {}
        self._alerts: deque[WhaleAlert] = deque(maxlen=alert_limit)

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
                finalized_amount = state.bucket_amount
                finalized_buy_volume = state.bucket_buy_volume
                finalized_sell_volume = state.bucket_sell_volume

                if len(state.previous_amounts) == self._WINDOW_SIZE:
                    average_amount = sum(state.previous_amounts, Decimal()) / self._WINDOW_SIZE
                    alert_type = self._classify(finalized_amount, average_amount, thresholds)
                    if alert_type is not None:
                        generated.append(
                            self._emit(
                                chain,
                                contract,
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
                                    chain,
                                    contract,
                                    WhaleAlertType.SUSTAINED_FLOW,
                                    sustained_total,
                                    sum(state.sustained_buy_volumes, Decimal()),
                                    sum(state.sustained_sell_volumes, Decimal()),
                                )
                            )
                    else:
                        state.sustained_alerted = False

                state.previous_amounts.append(finalized_amount)
                state.bucket_start = current_bucket_start
                state.bucket_amount = Decimal(0)
                state.bucket_buy_volume = Decimal(0)
                state.bucket_sell_volume = Decimal(0)

            state.bucket_amount += amount
            state.bucket_buy_volume += buy_volume
            state.bucket_sell_volume += sell_volume

        return tuple(generated)

    def _emit(
        self,
        chain: OptionChain,
        contract: OptionContract,
        alert_type: WhaleAlertType,
        amount: Decimal,
        estimated_buy_volume: Decimal,
        estimated_sell_volume: Decimal,
    ) -> WhaleAlert:
        alert = WhaleAlert(
            symbol=chain.symbol,
            occ_symbol=contract.occ_symbol,
            alert_type=alert_type,
            amount=amount,
            as_of=chain.as_of,
            estimated_buy_volume=estimated_buy_volume,
            estimated_sell_volume=estimated_sell_volume,
        )
        self._alerts.append(alert)
        return alert

    def recent_alerts(self, symbol: str, limit: int = 100) -> tuple[WhaleAlert, ...]:
        normalized = symbol.upper()
        matches = (alert for alert in reversed(self._alerts) if alert.symbol == normalized)
        return tuple(alert for _, alert in zip(range(limit), matches, strict=False))

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
