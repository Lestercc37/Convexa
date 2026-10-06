from __future__ import annotations

from backend.domain.entities import Underlying, UnderlyingKind

# Trimmed 2026-10-06 (owner's decision, after the open showed the stream near its capacity limit):
# TSLA, META, AMZN, GOOGL, AAPL, MSFT and DIA were removed; ES stays and NQ is planned as a
# proxy of NDX (see futures_proxy.PRICE_PROXY_SYMBOL_BY_FUTURE). Stored history is untouched.
ACTIVE_UNDERLYINGS: tuple[Underlying, ...] = (
    Underlying("SPY", UnderlyingKind.EQUITY, True),
    Underlying("QQQ", UnderlyingKind.EQUITY, True),
    Underlying("IWM", UnderlyingKind.EQUITY, True),
    Underlying("SPX", UnderlyingKind.INDEX, True),
    Underlying("VIX", UnderlyingKind.INDEX, True),
    Underlying("NDX", UnderlyingKind.INDEX, True),
    Underlying("NVDA", UnderlyingKind.EQUITY, True),
    Underlying("ES", UnderlyingKind.FUTURE, True),
    Underlying("NQ", UnderlyingKind.FUTURE, True),
)

ACTIVE_UNDERLYINGS_BY_SYMBOL: dict[str, Underlying] = {
    underlying.symbol: underlying for underlying in ACTIVE_UNDERLYINGS
}
