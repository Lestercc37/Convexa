from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from backend.domain.entities import OptionChain, OptionContract


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


class WhaleAlertsEngine:
    """Detect unusual contract volume from provider-independent chain snapshots.

    Each call to `process()` carries one raw reading (today, roughly every
    30s, whenever the internal trigger fires — the engine itself has no
    fixed cadence). Readings are grouped into real calendar-minute buckets
    using `chain.as_of` (same floor-to-minute approach the frontend already
    uses for candles), and only a *finalized* 1-minute bucket is ever
    classified or windowed — never a raw sub-minute reading.
    """

    _WINDOW_SIZE = 5
    _SUSTAINED_WINDOW_SIZE = 15
    _CONTRACT_MULTIPLIER = Decimal(100)

    def __init__(
        self,
        thresholds_by_symbol: dict[str, WhaleAlertThresholds] | None = None,
        default_thresholds: WhaleAlertThresholds | None = None,
        alert_limit: int = 1000,
    ) -> None:
        self._default_thresholds = default_thresholds or WhaleAlertThresholds()
        self._thresholds_by_symbol = {
            symbol.upper(): thresholds
            for symbol, thresholds in (thresholds_by_symbol or {}).items()
        }
        self._states: dict[str, _ContractState] = {}
        self._alerts: deque[WhaleAlert] = deque(maxlen=alert_limit)

    def process(self, chain: OptionChain) -> tuple[WhaleAlert, ...]:
        generated: list[WhaleAlert] = []
        thresholds = self._thresholds_by_symbol.get(chain.symbol, self._default_thresholds)
        current_bucket_start = _floor_to_minute(chain.as_of)

        for contract in chain.contracts:
            state = self._states.get(contract.occ_symbol)
            if state is None:
                self._states[contract.occ_symbol] = _ContractState(
                    cumulative_volume=contract.volume,
                    bucket_start=current_bucket_start,
                )
                continue

            delta = contract.volume - state.cumulative_volume
            state.cumulative_volume = contract.volume
            if delta < 0:
                # Session rollover — the volume counter is no longer
                # comparable to anything accumulated so far, so every
                # window (whale/unusual, sustained flow, the in-progress
                # bucket) is discarded, same treatment the original code
                # already gave `previous_amounts`.
                state.previous_amounts.clear()
                state.sustained_amounts.clear()
                state.sustained_alerted = False
                state.bucket_amount = Decimal(0)
                state.bucket_start = current_bucket_start
                continue

            amount = Decimal(delta) * contract.last * self._CONTRACT_MULTIPLIER

            if current_bucket_start != state.bucket_start:
                finalized_amount = state.bucket_amount

                if len(state.previous_amounts) == self._WINDOW_SIZE:
                    average_amount = sum(state.previous_amounts, Decimal()) / self._WINDOW_SIZE
                    alert_type = self._classify(finalized_amount, average_amount, thresholds)
                    if alert_type is not None:
                        generated.append(
                            self._emit(chain, contract, alert_type, finalized_amount)
                        )

                state.sustained_amounts.append(finalized_amount)
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
                                )
                            )
                    else:
                        state.sustained_alerted = False

                state.previous_amounts.append(finalized_amount)
                state.bucket_start = current_bucket_start
                state.bucket_amount = Decimal(0)

            state.bucket_amount += amount

        return tuple(generated)

    def _emit(
        self,
        chain: OptionChain,
        contract: OptionContract,
        alert_type: WhaleAlertType,
        amount: Decimal,
    ) -> WhaleAlert:
        alert = WhaleAlert(
            symbol=chain.symbol,
            occ_symbol=contract.occ_symbol,
            alert_type=alert_type,
            amount=amount,
            as_of=chain.as_of,
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
