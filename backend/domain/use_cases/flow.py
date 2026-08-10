from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from backend.domain.entities import OptionChain


class WhaleAlertType(StrEnum):
    UNUSUAL = "UNUSUAL"
    WHALE = "WHALE"


@dataclass(frozen=True, slots=True)
class WhaleAlertThresholds:
    unusual_min: Decimal = Decimal("40000.0")
    whale_min: Decimal = Decimal("150000.0")
    unusual_multiplier: Decimal = Decimal("3.0")
    whale_multiplier: Decimal = Decimal("6.0")


@dataclass(frozen=True, slots=True)
class WhaleAlert:
    symbol: str
    occ_symbol: str
    alert_type: WhaleAlertType
    amount: Decimal
    as_of: datetime


@dataclass(slots=True)
class _ContractState:
    cumulative_volume: int
    previous_amounts: deque[Decimal]


class WhaleAlertsEngine:
    """Detect unusual contract volume from provider-independent chain snapshots."""

    _WINDOW_SIZE = 5
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

        for contract in chain.contracts:
            state = self._states.get(contract.occ_symbol)
            if state is None:
                self._states[contract.occ_symbol] = _ContractState(
                    cumulative_volume=contract.volume,
                    previous_amounts=deque(maxlen=self._WINDOW_SIZE),
                )
                continue

            delta = contract.volume - state.cumulative_volume
            state.cumulative_volume = contract.volume
            if delta < 0:
                state.previous_amounts.clear()
                continue

            amount = Decimal(delta) * contract.last * self._CONTRACT_MULTIPLIER
            if len(state.previous_amounts) == self._WINDOW_SIZE:
                average_amount = sum(state.previous_amounts, Decimal()) / self._WINDOW_SIZE
                alert_type = self._classify(amount, average_amount, thresholds)
                if alert_type is not None:
                    alert = WhaleAlert(
                        symbol=chain.symbol,
                        occ_symbol=contract.occ_symbol,
                        alert_type=alert_type,
                        amount=amount,
                        as_of=chain.as_of,
                    )
                    self._alerts.append(alert)
                    generated.append(alert)

            state.previous_amounts.append(amount)

        return tuple(generated)

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
