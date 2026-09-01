"""Feeds WhaleAlertsEngine.process_trade() from a live provider.

Concurrently consumes a provider's Trade Stream and Quote Stream (both
IDataProvider port methods — never a concrete adapter import, same rule
every other use case in this package already follows) and keeps the
"last known bid/ask per contract" state Lee-Ready's quote rule needs.

No time-indexed quote history — just a plain dict, replaced whenever a
newer QuoteEvent for that occ_symbol arrives. "Vigente en ese instante"
on a live stream reduces to "most recently received," the same timestamp
precision the rest of this codebase already has for trades (FlowEvent.
as_of is stamped at local receipt time too, not a reconciled on-exchange
timestamp) — see LatestQuote's own docstring.
"""

from __future__ import annotations

import asyncio

from backend.domain.entities import LatestQuote
from backend.domain.ports import IDataProvider
from backend.domain.use_cases.flow import WhaleAlertsEngine


class StreamWhaleAlertsUseCase:
    def __init__(self, provider: IDataProvider, engine: WhaleAlertsEngine) -> None:
        self._provider = provider
        self._engine = engine
        self._latest_quotes: dict[str, LatestQuote] = {}

    async def run(self, underlying: str) -> None:
        """Consume both streams for `underlying` until cancelled.

        Runs forever for a real streaming provider — callers own the
        task lifecycle (see core/whale_alerts_stream.py) and cancel it
        on shutdown, same pattern as ThetaTradeStream/ThetaQuoteStream's
        own `stop()`. Completes immediately for a provider with nothing
        to stream (e.g. MockDataProvider — both of its stream methods
        are an immediately-exhausted async generator).
        """
        await asyncio.gather(
            self._consume_quotes(underlying),
            self._consume_trades(underlying),
        )

    async def _consume_quotes(self, underlying: str) -> None:
        async for quote_event in self._provider.stream_quotes(underlying):
            self._latest_quotes[quote_event.occ_symbol] = LatestQuote(
                bid=quote_event.bid,
                ask=quote_event.ask,
                as_of=quote_event.as_of,
            )

    async def _consume_trades(self, underlying: str) -> None:
        async for trade_event in self._provider.stream_trades(underlying):
            quote = self._latest_quotes.get(trade_event.occ_symbol)
            self._engine.process_trade(trade_event, quote)
