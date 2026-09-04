from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.domain.entities import (
    AggressorSide,
    ContractType,
    DailyBar,
    DailyGammaReference,
    FlowEvent,
    FlowEventType,
    GammaAggregate,
    Greeks,
    MarketPrice,
    OptionChain,
    OptionContract,
    UnderlyingKind,
)
from backend.domain.underlyings import ACTIVE_UNDERLYINGS_BY_SYMBOL
from backend.domain.use_cases.flow import WhaleAlert, WhaleAlertType


class AsyncPostgreSQLStorage:
    """Async counterpart to `PostgreSQLStorage`, covering the handful of
    reads `/gamma/{symbol}` and `/market/{symbol}` need, plus one write
    (`save_market_price`) for `StreamUnderlyingPriceUseCase` -- confirmed
    live, 2026-09: that use case used to call the plain synchronous
    `IStorage.save_market_price` directly (unawaited, no thread) from
    inside its `async def run()` loop, blocking the event loop on every
    persisted tick. Also `get_recent_whale_alerts`, added for
    `whale_alerts`'s new persistence -- `/alerts/{symbol}` and the
    screener don't read this yet (still in-memory `WhaleAlertsEngine`
    in this phase), but the async read is built now, on the same
    pattern, so nothing blocks that migration when it's approved.

    Deliberately not a full `IStorage` implementation otherwise (no
    `get_latest_chain_snapshot(expiration=...)` filter, no
    `_gamma_aggregate_items` join) -- `/gamma`/`/market`'s own
    serializers never read them, and every other write in this codebase
    still goes through the scheduler's existing sync `PostgreSQLStorage`.
    Same SQL as the sync methods this mirrors in `postgresql.py` -- kept
    in sync with those queries by hand, since duplicating a handful of
    read-only SELECTs was simpler than threading a shared query builder
    through two different SQLAlchemy execution styles (sync `Session` vs
    `AsyncSession`).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def get_latest_gamma_aggregate(self, underlying: str) -> GammaAggregate | None:
        async with self.session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT g.time, g.underlying_id, u.symbol, g.gamma_flip, g.call_wall,
                           g.put_wall, g.max_pain, g.net_gamma,
                           g.dealer_gamma_notional, g.vega_exposure,
                           g.theta_exposure, g.charm_exposure,
                           g.vanna_exposure,
                           g.absolute_gamma_strike,
                           g.total_market_gamma, g.positive_gamma, g.negative_gamma,
                           g.peak_gamma_value
                    FROM gamma_aggregates AS g
                    JOIN underlyings AS u ON u.id = g.underlying_id
                    WHERE u.symbol = :symbol
                    ORDER BY g.time DESC
                    LIMIT 1
                    """
                ),
                {"symbol": underlying.upper()},
            )
            row = result.mappings().one_or_none()
        if row is None:
            return None
        return GammaAggregate(
            symbol=str(row["symbol"]),
            as_of=row["time"],
            gamma_flip=(Decimal(row["gamma_flip"]) if row["gamma_flip"] is not None else None),
            call_wall=Decimal(row["call_wall"]),
            put_wall=Decimal(row["put_wall"]),
            max_pain=Decimal(row["max_pain"]),
            net_gamma=Decimal(row["net_gamma"]),
            dealer_gamma_notional=Decimal(row["dealer_gamma_notional"]),
            vega_exposure=Decimal(row["vega_exposure"]),
            theta_exposure=Decimal(row["theta_exposure"]),
            charm_exposure=Decimal(row["charm_exposure"]),
            vanna_exposure=Decimal(row["vanna_exposure"]),
            absolute_gamma_strike=Decimal(row["absolute_gamma_strike"]),
            total_market_gamma=Decimal(row["total_market_gamma"]),
            positive_gamma=Decimal(row["positive_gamma"]),
            negative_gamma=Decimal(row["negative_gamma"]),
            peak_gamma_value=Decimal(row["peak_gamma_value"]),
        )

    async def get_latest_price(self, underlying: str) -> MarketPrice | None:
        async with self.session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT m.time, u.symbol, m.price, m.volume
                    FROM market_snapshots AS m
                    JOIN underlyings AS u ON u.id = m.underlying_id
                    WHERE u.symbol = :symbol
                    ORDER BY m.time DESC
                    LIMIT 1
                    """
                ),
                {"symbol": underlying.upper()},
            )
            row = result.mappings().one_or_none()
        if row is None:
            return None
        return MarketPrice(
            symbol=str(row["symbol"]),
            as_of=row["time"],
            price=Decimal(row["price"]),
            volume=int(row["volume"]),
        )

    async def get_price_history(
        self, underlying: str, start: datetime, end: datetime
    ) -> list[MarketPrice]:
        async with self.session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT m.time, u.symbol, m.price, m.volume
                    FROM market_snapshots AS m
                    JOIN underlyings AS u ON u.id = m.underlying_id
                    WHERE u.symbol = :symbol
                      AND m.time BETWEEN :start AND :end
                    ORDER BY m.time
                    """
                ),
                {"symbol": underlying.upper(), "start": start, "end": end},
            )
            rows = result.mappings().all()
        return [
            MarketPrice(
                symbol=str(row["symbol"]),
                as_of=row["time"],
                price=Decimal(row["price"]),
                volume=int(row["volume"]),
            )
            for row in rows
        ]

    async def get_latest_chain_snapshot(self, underlying: str) -> OptionChain | None:
        async with self.session_factory() as session:
            result = await session.execute(
                text(
                    """
                    WITH latest AS (
                        SELECT MAX(s.time) AS time
                        FROM option_chain_snapshots AS s
                        JOIN option_contracts AS oc ON oc.id = s.contract_id
                        JOIN underlyings AS u ON u.id = oc.underlying_id
                        WHERE u.symbol = :symbol
                    )
                    SELECT
                        s.time, s.spot_price, oc.strike, oc.expiration,
                        oc.contract_type, oc.occ_symbol, s.bid, s.ask, s.last,
                        s.volume, s.open_interest, s.iv, s.delta, s.gamma,
                        s.theta, s.vega, s.charm, s.vanna
                    FROM option_chain_snapshots AS s
                    JOIN option_contracts AS oc ON oc.id = s.contract_id
                    JOIN underlyings AS u ON u.id = oc.underlying_id
                    JOIN latest ON latest.time = s.time
                    WHERE u.symbol = :symbol
                    ORDER BY oc.expiration, oc.strike, oc.contract_type
                    """
                ),
                {"symbol": underlying.upper()},
            )
            rows = result.mappings().all()
        if not rows:
            return None
        contracts = tuple(
            OptionContract(
                underlying=underlying,
                strike=Decimal(row["strike"]),
                expiration=row["expiration"],
                contract_type=ContractType(str(row["contract_type"])),
                occ_symbol=str(row["occ_symbol"]),
                bid=Decimal(row["bid"]),
                ask=Decimal(row["ask"]),
                last=Decimal(row["last"]),
                volume=int(row["volume"]),
                open_interest=int(row["open_interest"]),
                iv=Decimal(row["iv"]),
                greeks=Greeks(
                    delta=Decimal(row["delta"]),
                    gamma=Decimal(row["gamma"]),
                    theta=Decimal(row["theta"]),
                    vega=Decimal(row["vega"]),
                    charm=Decimal(row["charm"]),
                    vanna=Decimal(row["vanna"]),
                ),
            )
            for row in rows
        )
        return OptionChain(
            symbol=underlying,
            as_of=rows[0]["time"],
            spot_price=Decimal(rows[0]["spot_price"]),
            contracts=contracts,
        )

    async def get_daily_bars(self, underlying: str, limit: int = 15) -> list[DailyBar]:
        async with self.session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT b.date, u.symbol, b.open, b.high, b.low, b.close
                    FROM daily_bars AS b
                    JOIN underlyings AS u ON u.id = b.underlying_id
                    WHERE u.symbol = :symbol
                    ORDER BY b.date DESC
                    LIMIT :limit
                    """
                ),
                {"symbol": underlying.upper(), "limit": limit},
            )
            rows = result.mappings().all()
        return [
            DailyBar(
                date=row["date"],
                symbol=str(row["symbol"]),
                open_price=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
            )
            for row in rows
        ]

    async def get_recent_flow(self, underlying: str, limit: int = 20) -> list[FlowEvent]:
        async with self.session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT f.time, u.symbol, oc.occ_symbol, f.event_type,
                           f.premium, f.size, f.aggressor_side
                    FROM flow_events AS f
                    JOIN option_contracts AS oc ON oc.id = f.contract_id
                    JOIN underlyings AS u ON u.id = oc.underlying_id
                    WHERE u.symbol = :symbol
                    ORDER BY f.time DESC
                    LIMIT :limit
                    """
                ),
                {"symbol": underlying.upper(), "limit": limit},
            )
            rows = result.mappings().all()
        return [
            FlowEvent(
                symbol=str(row["symbol"]),
                occ_symbol=str(row["occ_symbol"]),
                as_of=row["time"],
                event_type=FlowEventType(str(row["event_type"])),
                premium=Decimal(row["premium"]),
                size=int(row["size"]),
                aggressor_side=AggressorSide(str(row["aggressor_side"])),
            )
            for row in rows
        ]

    async def get_daily_gamma_references(
        self, underlying: str, limit: int = 60
    ) -> list[DailyGammaReference]:
        async with self.session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT r.date, u.symbol, r.net_gamma,
                           r.pc_oi_ratio, r.skew_25d, r.atm_iv
                    FROM daily_gamma_reference AS r
                    JOIN underlyings AS u ON u.id = r.underlying_id
                    WHERE u.symbol = :symbol
                    ORDER BY r.date DESC
                    LIMIT :limit
                    """
                ),
                {"symbol": underlying.upper(), "limit": limit},
            )
            rows = result.mappings().all()
        return [
            DailyGammaReference(
                date=row["date"],
                symbol=str(row["symbol"]),
                net_gamma=Decimal(row["net_gamma"]),
                pc_oi_ratio=Decimal(row["pc_oi_ratio"]),
                skew_25d=Decimal(row["skew_25d"]),
                atm_iv=Decimal(row["atm_iv"]),
            )
            for row in rows
        ]

    async def get_recent_whale_alerts(self, underlying: str, limit: int = 100) -> list[WhaleAlert]:
        async with self.session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT w.time, u.symbol, w.occ_symbol, w.alert_type, w.amount,
                           w.estimated_buy_volume, w.estimated_sell_volume
                    FROM whale_alerts AS w
                    JOIN underlyings AS u ON u.id = w.underlying_id
                    WHERE u.symbol = :symbol
                    ORDER BY w.time DESC
                    LIMIT :limit
                    """
                ),
                {"symbol": underlying.upper(), "limit": limit},
            )
            rows = result.mappings().all()
        return [
            WhaleAlert(
                symbol=str(row["symbol"]),
                occ_symbol=str(row["occ_symbol"]),
                alert_type=WhaleAlertType(str(row["alert_type"])),
                amount=Decimal(row["amount"]),
                as_of=row["time"],
                estimated_buy_volume=Decimal(row["estimated_buy_volume"]),
                estimated_sell_volume=Decimal(row["estimated_sell_volume"]),
            )
            for row in rows
        ]

    async def save_market_price(self, price: MarketPrice) -> None:
        async with self.session_factory.begin() as session:
            underlying_id = await self._ensure_underlying(session, price.symbol)
            await session.execute(
                text(
                    """
                    INSERT INTO market_snapshots (time, underlying_id, price, volume)
                    VALUES (:time, :underlying_id, :price, :volume)
                    """
                ),
                {
                    "time": price.as_of,
                    "underlying_id": underlying_id,
                    "price": price.price,
                    "volume": price.volume,
                },
            )

    @staticmethod
    async def _ensure_underlying(session: AsyncSession, symbol: str) -> int:
        normalized_symbol = symbol.upper()
        configured = ACTIVE_UNDERLYINGS_BY_SYMBOL.get(normalized_symbol)
        kind = configured.kind.value if configured is not None else UnderlyingKind.EQUITY.value
        is_priority = configured.is_priority if configured is not None else False
        conflict_action = (
            "kind = EXCLUDED.kind, is_priority = EXCLUDED.is_priority"
            if configured is not None
            else "symbol = EXCLUDED.symbol"
        )
        result = await session.execute(
            text(
                f"""
                INSERT INTO underlyings (symbol, kind, is_priority)
                VALUES (:symbol, :kind, :is_priority)
                ON CONFLICT (symbol) DO UPDATE SET {conflict_action}
                RETURNING id
                """
            ),
            {
                "symbol": normalized_symbol,
                "kind": kind,
                "is_priority": is_priority,
            },
        )
        return result.scalar_one()
