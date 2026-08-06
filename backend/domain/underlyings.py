from __future__ import annotations

from backend.domain.entities import Underlying, UnderlyingKind

ACTIVE_UNDERLYINGS: tuple[Underlying, ...] = (
    Underlying("SPY", UnderlyingKind.EQUITY, True),
    Underlying("QQQ", UnderlyingKind.EQUITY, True),
    Underlying("IWM", UnderlyingKind.EQUITY, True),
    Underlying("SPX", UnderlyingKind.INDEX, True),
    Underlying("VIX", UnderlyingKind.INDEX, True),
    Underlying("TSLA", UnderlyingKind.EQUITY, True),
    Underlying("NVDA", UnderlyingKind.EQUITY, True),
    Underlying("META", UnderlyingKind.EQUITY, True),
    Underlying("AMZN", UnderlyingKind.EQUITY, True),
    Underlying("GOOGL", UnderlyingKind.EQUITY, True),
)

ACTIVE_UNDERLYINGS_BY_SYMBOL: dict[str, Underlying] = {
    underlying.symbol: underlying for underlying in ACTIVE_UNDERLYINGS
}
