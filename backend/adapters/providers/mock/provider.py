from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import AsyncIterator

from backend.domain.entities import (
    ContractType,
    DailyBar,
    FlowEvent,
    Greeks,
    MarketSnapshot,
    OptionChain,
    OptionContract,
    utc_now,
)


class MockDataProvider:
    """Deterministic IDataProvider adapter for tests and local development."""

    def get_option_chain(self, underlying: str, expiration: date | None = None) -> OptionChain:
        symbol = underlying.upper()
        as_of = datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc)
        exp = expiration or date(2026, 2, 20)
        contracts = tuple(
            _contract(symbol, strike, exp, contract_type)
            for strike in (Decimal("540"), Decimal("545"), Decimal("550"))
            for contract_type in (ContractType.CALL, ContractType.PUT)
        )
        return OptionChain(
            symbol=symbol,
            as_of=as_of,
            spot_price=Decimal("552.25"),
            contracts=contracts,
        )

    def get_underlying_snapshot(self, underlying: str) -> MarketSnapshot:
        return MarketSnapshot(
            symbol=underlying.upper(),
            as_of=utc_now(),
            price=Decimal("552.25"),
            volume=1_250_000,
            pc_oi_ratio=Decimal("1.10"),
            skew_25d=Decimal("0.04"),
            atm_iv=Decimal("0.22"),
        )

    def get_daily_bars(self, underlying: str, days: int = 20) -> list[DailyBar]:
        symbol = underlying.upper()
        end_date = date(2026, 1, 14)  # the day before get_option_chain's fixed as_of
        base_close = Decimal("550.00")
        bars = []
        for offset in range(days - 1, -1, -1):
            bar_date = end_date - timedelta(days=offset)
            wobble = Decimal(offset % 5) - Decimal(2)  # deterministic -2..2 oscillation
            close = base_close + wobble
            bars.append(
                DailyBar(
                    symbol=symbol,
                    date=bar_date,
                    open_price=close - Decimal("0.50"),
                    high=close + Decimal("1.00"),
                    low=close - Decimal("1.00"),
                    close=close,
                )
            )
        return bars

    async def stream_trades(self, underlying: str) -> AsyncIterator[FlowEvent]:
        if False:
            yield

    async def start(self) -> None:
        """No persistent connection to open — deterministic, in-process data."""

    async def stop(self) -> None:
        """No persistent connection to close."""


def _contract(
    symbol: str, strike: Decimal, expiration: date, contract_type: ContractType
) -> OptionContract:
    suffix = "C" if contract_type == ContractType.CALL else "P"
    return OptionContract(
        underlying=symbol,
        strike=strike,
        expiration=expiration,
        contract_type=contract_type,
        occ_symbol=f"{symbol}{expiration:%y%m%d}{suffix}{int(strike * 1000):08d}",
        bid=Decimal("1.20"),
        ask=Decimal("1.25"),
        last=Decimal("1.22"),
        volume=3400,
        open_interest=8000,
        iv=Decimal("0.18"),
        greeks=Greeks(
            delta=Decimal("0.42") if contract_type == ContractType.CALL else Decimal("-0.40"),
            gamma=Decimal("0.03"),
            theta=Decimal("-0.015"),
            vega=Decimal("0.12"),
            charm=Decimal("-0.001") if contract_type == ContractType.CALL else Decimal("0.001"),
            vanna=Decimal("0.02") if contract_type == ContractType.CALL else Decimal("-0.02"),
        ),
    )
