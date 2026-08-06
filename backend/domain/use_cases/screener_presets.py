from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Iterable

from backend.domain.ports import IStorage
from backend.domain.use_cases.flow import EagleAlert, EagleAlertType


class ScreenerPreset(StrEnum):
    UNUSUAL_OPTIONS_ACTIVITY = "unusual-options-activity"
    NEGATIVE_GAMMA_BOARD = "negative-gamma-board"
    MAX_PAIN_KEY_LEVELS = "max-pain-key-levels"
    VANNA_EXPOSURE_LEADERS = "vanna-exposure-leaders"
    CHARM_DECAY_PRESSURE = "charm-decay-pressure"

    @classmethod
    def parse(cls, value: str) -> ScreenerPreset:
        return cls(value.strip().lower().replace("_", "-").replace(" ", "-"))


@dataclass(frozen=True, slots=True)
class ScreenerPresetResult:
    symbol: str
    as_of: datetime
    contract: str | None = None
    alert_type: EagleAlertType | None = None
    amount: Decimal | None = None
    net_gamma: Decimal | None = None
    gamma_flip: Decimal | None = None
    call_wall: Decimal | None = None
    put_wall: Decimal | None = None
    max_pain: Decimal | None = None
    vanna_exposure: Decimal | None = None
    charm_exposure: Decimal | None = None


def get_screener_preset(
    storage: IStorage,
    preset: ScreenerPreset,
    alerts: Iterable[EagleAlert] = (),
) -> list[ScreenerPresetResult]:
    if preset is ScreenerPreset.UNUSUAL_OPTIONS_ACTIVITY:
        return sorted(
            (
                ScreenerPresetResult(
                    symbol=alert.symbol,
                    as_of=alert.as_of,
                    contract=alert.occ_symbol,
                    alert_type=alert.alert_type,
                    amount=alert.amount,
                )
                for alert in alerts
            ),
            key=lambda item: item.as_of,
            reverse=True,
        )

    aggregates = [
        aggregate
        for underlying in storage.list_underlyings()
        if (aggregate := storage.get_latest_gamma_aggregate(underlying.symbol)) is not None
    ]

    if preset is ScreenerPreset.NEGATIVE_GAMMA_BOARD:
        return [
            ScreenerPresetResult(
                symbol=aggregate.symbol,
                as_of=aggregate.as_of,
                net_gamma=aggregate.net_gamma,
            )
            for aggregate in sorted(
                (item for item in aggregates if item.net_gamma < 0),
                key=lambda item: abs(item.net_gamma),
                reverse=True,
            )
        ]

    if preset is ScreenerPreset.MAX_PAIN_KEY_LEVELS:
        return [
            ScreenerPresetResult(
                symbol=aggregate.symbol,
                as_of=aggregate.as_of,
                gamma_flip=aggregate.gamma_flip,
                call_wall=aggregate.call_wall,
                put_wall=aggregate.put_wall,
                max_pain=aggregate.max_pain,
            )
            for aggregate in aggregates
        ]

    exposure_name = (
        "vanna_exposure"
        if preset is ScreenerPreset.VANNA_EXPOSURE_LEADERS
        else "charm_exposure"
    )
    ranked = sorted(
        aggregates,
        key=lambda item: abs(getattr(item, exposure_name)),
        reverse=True,
    )
    return [
        ScreenerPresetResult(
            symbol=aggregate.symbol,
            as_of=aggregate.as_of,
            vanna_exposure=(
                aggregate.vanna_exposure
                if preset is ScreenerPreset.VANNA_EXPOSURE_LEADERS
                else None
            ),
            charm_exposure=(
                aggregate.charm_exposure
                if preset is ScreenerPreset.CHARM_DECAY_PRESSURE
                else None
            ),
        )
        for aggregate in ranked
    ]
