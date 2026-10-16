"""Keeps the large prints of the proxy ETFs (SPY/QQQ) with the venue and condition the price chart does not keep.

Why: a 2026-10-16 look at SPY (docs/hm_spy_trades_day.py, one day, descriptive) found that 75% of the shares traded were reported by the FINRA/Nasdaq
TRF (exchange 57, i.e. off-exchange) and that 648 prints were over $1M, but most of the very large ones were NOT at market: average-price prints
(condition 3) and contingent prints (condition 124) at a fixed price ~1% away from the quote. Only two were at market. The stream already carries the
exchange and the condition of every stock trade; the live code dropped the exchange and kept no print at all. Storing the few hundred large prints a
day, each marked at market or not, lets "does a big off-exchange print act as support or resistance?" be asked of real data later instead of
bought again from ThetaData day by day.

Nothing here touches the displayed price: `StreamUnderlyingPriceUseCase` is unchanged. The recorder only observes a trade the processor already
parsed, in memory, and the rows are flushed in batches from a separate task (stream_processor_worker.py), so the hot path is a dict lookup and, for
the rare large print, one list append.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from backend.domain.entities import LargeTradePrint, UnderlyingTradeEvent
from backend.domain.use_cases.stream_underlying_price import sets_last_price

LARGE_PRINT_SYMBOLS: frozenset[str] = frozenset({"SPY", "QQQ"})
LARGE_PRINT_MIN_NOTIONAL = Decimal(1_000_000)
# "At market": within 0.1% of the last price that was allowed to set the displayed price.
AT_MARKET_TOLERANCE = Decimal("0.001")
# A flush that keeps failing must not grow memory without bound; the oldest rows go first.
MAX_PENDING_ROWS = 20_000


class LargePrintRecorder:
    def __init__(
        self,
        symbols: frozenset[str] = LARGE_PRINT_SYMBOLS,
        min_notional: Decimal = LARGE_PRINT_MIN_NOTIONAL,
        tolerance: Decimal = AT_MARKET_TOLERANCE,
    ) -> None:
        self._symbols = symbols
        self._min_notional = min_notional
        self._tolerance = tolerance
        self._pending: list[LargeTradePrint] = []
        self._last_price: dict[str, Decimal] = {}

    def observe(self, event: UnderlyingTradeEvent, exchange_time: datetime | None = None) -> None:
        if event.symbol not in self._symbols:
            return
        reference = self._last_price.get(event.symbol)
        is_large = event.price * event.size >= self._min_notional
        at_market: bool | None = None
        if is_large:
            if reference is not None and reference > 0:
                at_market = abs(event.price - reference) / reference <= self._tolerance
            self._pending.append(
                LargeTradePrint(
                    symbol=event.symbol,
                    time=exchange_time if exchange_time is not None else event.as_of,
                    price=event.price,
                    size=event.size,
                    condition=event.condition,
                    exchange=event.exchange,
                    at_market=at_market,
                    received_at=event.as_of,
                )
            )
            if len(self._pending) > MAX_PENDING_ROWS:
                del self._pending[: len(self._pending) - MAX_PENDING_ROWS]
        # A large print that is itself away from the market must not become the reference for the next ones.
        if sets_last_price(event) and at_market is not False:
            self._last_price[event.symbol] = event.price

    def take(self) -> list[LargeTradePrint]:
        rows, self._pending = self._pending, []
        return rows

    def put_back(self, rows: list[LargeTradePrint]) -> None:
        """A failed flush returns its rows to the front, ahead of anything captured meanwhile."""
        self._pending = rows + self._pending
        if len(self._pending) > MAX_PENDING_ROWS:
            del self._pending[: len(self._pending) - MAX_PENDING_ROWS]
