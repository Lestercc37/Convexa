from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session, sessionmaker

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
    Underlying,
    UnderlyingKind,
    WhaleThreshold,
)
from backend.domain.underlyings import ACTIVE_UNDERLYINGS_BY_SYMBOL


class PostgreSQLStorage:
    """Synchronous PostgreSQL implementation of the domain storage port."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def list_underlyings(self) -> list[Underlying]:
        with self.session_factory() as session:
            rows = session.execute(
                text(
                    """
                    SELECT symbol, kind, is_priority
                    FROM underlyings
                    ORDER BY symbol
                    """
                )
            ).mappings()
            return [
                Underlying(
                    symbol=str(row["symbol"]),
                    kind=UnderlyingKind(str(row["kind"])),
                    is_priority=bool(row["is_priority"]),
                )
                for row in rows
            ]

    def save_whale_threshold(self, threshold: WhaleThreshold) -> None:
        with self.session_factory.begin() as session:
            underlying_id = self._ensure_underlying(session, threshold.symbol)
            session.execute(
                text(
                    """
                    INSERT INTO whale_thresholds (
                        underlying_id, unusual_min, whale_min,
                        unusual_multiplier, whale_multiplier
                    )
                    VALUES (
                        :underlying_id, :unusual_min, :whale_min,
                        :unusual_multiplier, :whale_multiplier
                    )
                    ON CONFLICT (underlying_id) DO UPDATE SET
                        unusual_min = EXCLUDED.unusual_min,
                        whale_min = EXCLUDED.whale_min,
                        unusual_multiplier = EXCLUDED.unusual_multiplier,
                        whale_multiplier = EXCLUDED.whale_multiplier
                    """
                ),
                {
                    "underlying_id": underlying_id,
                    "unusual_min": str(threshold.unusual_min),
                    "whale_min": str(threshold.whale_min),
                    "unusual_multiplier": str(threshold.unusual_multiplier),
                    "whale_multiplier": str(threshold.whale_multiplier),
                },
            )

    def get_whale_thresholds(self) -> dict[str, WhaleThreshold]:
        with self.session_factory() as session:
            rows = session.execute(
                text(
                    """
                    SELECT u.symbol, w.unusual_min, w.whale_min,
                           w.unusual_multiplier, w.whale_multiplier
                    FROM whale_thresholds AS w
                    JOIN underlyings AS u ON u.id = w.underlying_id
                    ORDER BY u.symbol
                    """
                )
            ).mappings()
            return {
                str(row["symbol"]): WhaleThreshold(
                    symbol=str(row["symbol"]),
                    unusual_min=Decimal(str(row["unusual_min"])),
                    whale_min=Decimal(str(row["whale_min"])),
                    unusual_multiplier=Decimal(str(row["unusual_multiplier"])),
                    whale_multiplier=Decimal(str(row["whale_multiplier"])),
                )
                for row in rows
            }

    def save_chain_snapshot(self, chain: OptionChain) -> None:
        with self.session_factory.begin() as session:
            underlying_id = self._ensure_underlying(session, chain.symbol)
            for contract in chain.contracts:
                contract_id = session.execute(
                    text(
                        """
                        INSERT INTO option_contracts (
                            underlying_id, strike, expiration, contract_type, occ_symbol
                        )
                        VALUES (
                            :underlying_id, :strike, :expiration, :contract_type, :occ_symbol
                        )
                        ON CONFLICT (occ_symbol) DO UPDATE SET
                            underlying_id = EXCLUDED.underlying_id,
                            strike = EXCLUDED.strike,
                            expiration = EXCLUDED.expiration,
                            contract_type = EXCLUDED.contract_type
                        RETURNING id
                        """
                    ),
                    {
                        "underlying_id": underlying_id,
                        "strike": contract.strike,
                        "expiration": contract.expiration,
                        "contract_type": contract.contract_type.value,
                        "occ_symbol": contract.occ_symbol,
                    },
                ).scalar_one()
                session.execute(
                    text(
                        """
                        INSERT INTO option_chain_snapshots (
                            time, contract_id, bid, ask, last, volume,
                            open_interest, iv, delta, gamma, theta, vega,
                            charm, vanna, spot_price
                        )
                        VALUES (
                            :time, :contract_id, :bid, :ask, :last, :volume,
                            :open_interest, :iv, :delta, :gamma, :theta, :vega,
                            :charm, :vanna, :spot_price
                        )
                        """
                    ),
                    {
                        "time": chain.as_of,
                        "contract_id": contract_id,
                        "bid": contract.bid,
                        "ask": contract.ask,
                        "last": contract.last,
                        "volume": contract.volume,
                        "open_interest": contract.open_interest,
                        "iv": contract.iv,
                        "delta": contract.greeks.delta,
                        "gamma": contract.greeks.gamma,
                        "theta": contract.greeks.theta,
                        "vega": contract.greeks.vega,
                        "charm": contract.greeks.charm,
                        "vanna": contract.greeks.vanna,
                        "spot_price": chain.spot_price,
                    },
                )

    def get_latest_chain_snapshot(
        self, underlying: str, expiration: date | None = None
    ) -> OptionChain | None:
        expiration_filter = "AND oc.expiration = :expiration" if expiration else ""
        statement = text(
            f"""
            WITH latest AS (
                SELECT MAX(s.time) AS time
                FROM option_chain_snapshots AS s
                JOIN option_contracts AS oc ON oc.id = s.contract_id
                JOIN underlyings AS u ON u.id = oc.underlying_id
                WHERE u.symbol = :symbol
                {expiration_filter}
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
            {expiration_filter}
            ORDER BY oc.expiration, oc.strike, oc.contract_type
            """
        )
        parameters: dict[str, str | date] = {"symbol": underlying.upper()}
        if expiration is not None:
            parameters["expiration"] = expiration

        with self.session_factory() as session:
            rows = list(session.execute(statement, parameters).mappings())
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

    def save_gamma_aggregate(self, gamma: GammaAggregate) -> None:
        with self.session_factory.begin() as session:
            underlying_id = self._ensure_underlying(session, gamma.symbol)
            session.execute(
                text(
                    """
                    INSERT INTO gamma_aggregates (
                        time, underlying_id, gamma_flip, call_wall, put_wall,
                        max_pain, net_gamma, dealer_gamma_notional,
                        vega_exposure, theta_exposure, charm_exposure,
                        vanna_exposure,
                        absolute_gamma_strike
                    )
                    VALUES (
                        :time, :underlying_id, :gamma_flip, :call_wall, :put_wall,
                        :max_pain, :net_gamma, :dealer_gamma_notional,
                        :vega_exposure, :theta_exposure, :charm_exposure,
                        :vanna_exposure,
                        :absolute_gamma_strike
                    )
                    """
                ),
                {
                    "time": gamma.as_of,
                    "underlying_id": underlying_id,
                    "gamma_flip": gamma.gamma_flip,
                    "call_wall": gamma.call_wall,
                    "put_wall": gamma.put_wall,
                    "max_pain": gamma.max_pain,
                    "net_gamma": gamma.net_gamma,
                    "dealer_gamma_notional": gamma.dealer_gamma_notional,
                    "vega_exposure": gamma.vega_exposure,
                    "theta_exposure": gamma.theta_exposure,
                    "charm_exposure": gamma.charm_exposure,
                    "vanna_exposure": gamma.vanna_exposure,
                    "absolute_gamma_strike": gamma.absolute_gamma_strike,
                },
            )

    def get_latest_gamma_aggregate(self, underlying: str) -> GammaAggregate | None:
        with self.session_factory() as session:
            row = (
                session.execute(
                    text(
                        """
                    SELECT g.time, u.symbol, g.gamma_flip, g.call_wall,
                           g.put_wall, g.max_pain, g.net_gamma,
                           g.dealer_gamma_notional, g.vega_exposure,
                           g.theta_exposure, g.charm_exposure,
                           g.vanna_exposure,
                           g.absolute_gamma_strike
                    FROM gamma_aggregates AS g
                    JOIN underlyings AS u ON u.id = g.underlying_id
                    WHERE u.symbol = :symbol
                    ORDER BY g.time DESC
                    LIMIT 1
                    """
                    ),
                    {"symbol": underlying.upper()},
                )
                .mappings()
                .one_or_none()
            )
        return self._gamma_from_row(row) if row is not None else None

    def get_gamma_history(
        self, underlying: str, start: datetime, end: datetime
    ) -> list[GammaAggregate]:
        with self.session_factory() as session:
            rows = session.execute(
                text(
                    """
                    SELECT g.time, u.symbol, g.gamma_flip, g.call_wall,
                           g.put_wall, g.max_pain, g.net_gamma,
                           g.dealer_gamma_notional, g.vega_exposure,
                           g.theta_exposure, g.charm_exposure,
                           g.vanna_exposure,
                           g.absolute_gamma_strike
                    FROM gamma_aggregates AS g
                    JOIN underlyings AS u ON u.id = g.underlying_id
                    WHERE u.symbol = :symbol
                      AND g.time BETWEEN :start AND :end
                    ORDER BY g.time
                    """
                ),
                {"symbol": underlying.upper(), "start": start, "end": end},
            ).mappings()
            return [self._gamma_from_row(row) for row in rows]

    def save_market_price(self, price: MarketPrice) -> None:
        with self.session_factory.begin() as session:
            underlying_id = self._ensure_underlying(session, price.symbol)
            session.execute(
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

    def get_latest_price(self, underlying: str) -> MarketPrice | None:
        with self.session_factory() as session:
            row = (
                session.execute(
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
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return MarketPrice(
            symbol=str(row["symbol"]),
            as_of=row["time"],
            price=Decimal(row["price"]),
            volume=int(row["volume"]),
        )

    def get_price_history(
        self, underlying: str, start: datetime, end: datetime
    ) -> list[MarketPrice]:
        with self.session_factory() as session:
            rows = session.execute(
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
            ).mappings()
            return [
                MarketPrice(
                    symbol=str(row["symbol"]),
                    as_of=row["time"],
                    price=Decimal(row["price"]),
                    volume=int(row["volume"]),
                )
                for row in rows
            ]

    def save_flow_event(self, event: FlowEvent) -> None:
        with self.session_factory.begin() as session:
            contract_id = session.execute(
                text(
                    """
                    SELECT oc.id
                    FROM option_contracts AS oc
                    JOIN underlyings AS u ON u.id = oc.underlying_id
                    WHERE u.symbol = :symbol AND oc.occ_symbol = :occ_symbol
                    """
                ),
                {"symbol": event.symbol.upper(), "occ_symbol": event.occ_symbol},
            ).scalar_one_or_none()
            if contract_id is None:
                raise ValueError(
                    f"Option contract {event.occ_symbol} must exist before flow is saved"
                )
            session.execute(
                text(
                    """
                    INSERT INTO flow_events (
                        time, contract_id, event_type, premium, size, aggressor_side
                    )
                    VALUES (
                        :time, :contract_id, :event_type, :premium, :size,
                        :aggressor_side
                    )
                    """
                ),
                {
                    "time": event.as_of,
                    "contract_id": contract_id,
                    "event_type": event.event_type.value,
                    "premium": event.premium,
                    "size": event.size,
                    "aggressor_side": event.aggressor_side.value,
                },
            )

    def get_flow_events(
        self, underlying: str, since: datetime | None = None, limit: int = 100
    ) -> list[FlowEvent]:
        since_filter = "AND f.time >= :since" if since is not None else ""
        statement = text(
            f"""
            SELECT f.time, u.symbol, oc.occ_symbol, f.event_type,
                   f.premium, f.size, f.aggressor_side
            FROM flow_events AS f
            JOIN option_contracts AS oc ON oc.id = f.contract_id
            JOIN underlyings AS u ON u.id = oc.underlying_id
            WHERE u.symbol = :symbol
            {since_filter}
            ORDER BY f.time DESC
            LIMIT :limit
            """
        )
        parameters: dict[str, str | int | datetime] = {
            "symbol": underlying.upper(),
            "limit": limit,
        }
        if since is not None:
            parameters["since"] = since
        with self.session_factory() as session:
            rows = session.execute(statement, parameters).mappings()
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

    def get_recent_flow(self, underlying: str, limit: int = 20) -> list[FlowEvent]:
        return self.get_flow_events(underlying, limit=limit)

    def save_daily_gamma_reference(self, reference: DailyGammaReference) -> None:
        with self.session_factory.begin() as session:
            underlying_id = self._ensure_underlying(session, reference.symbol)
            session.execute(
                text(
                    """
                    INSERT INTO daily_gamma_reference (
                        date, underlying_id, net_gamma, pc_oi_ratio, skew_25d,
                        atm_iv
                    )
                    VALUES (
                        :date, :underlying_id, :net_gamma, :pc_oi_ratio, :skew_25d,
                        :atm_iv
                    )
                    ON CONFLICT (underlying_id, date) DO UPDATE SET
                        net_gamma = EXCLUDED.net_gamma,
                        pc_oi_ratio = EXCLUDED.pc_oi_ratio,
                        skew_25d = EXCLUDED.skew_25d,
                        atm_iv = EXCLUDED.atm_iv
                    """
                ),
                {
                    "date": reference.date,
                    "underlying_id": underlying_id,
                    "net_gamma": reference.net_gamma,
                    "pc_oi_ratio": reference.pc_oi_ratio,
                    "skew_25d": reference.skew_25d,
                    "atm_iv": reference.atm_iv,
                },
            )

    def get_daily_gamma_references(
        self, underlying: str, limit: int = 60
    ) -> list[DailyGammaReference]:
        with self.session_factory() as session:
            rows = session.execute(
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
            ).mappings()
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

    def save_daily_bar(self, bar: DailyBar) -> None:
        with self.session_factory.begin() as session:
            underlying_id = self._ensure_underlying(session, bar.symbol)
            session.execute(
                text(
                    """
                    INSERT INTO daily_bars (
                        date, underlying_id, open, high, low, close
                    )
                    VALUES (
                        :date, :underlying_id, :open, :high, :low, :close
                    )
                    ON CONFLICT (underlying_id, date) DO UPDATE SET
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        close = EXCLUDED.close
                    """
                ),
                {
                    "date": bar.date,
                    "underlying_id": underlying_id,
                    "open": bar.open_price,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                },
            )

    def get_daily_bars(self, underlying: str, limit: int = 15) -> list[DailyBar]:
        with self.session_factory() as session:
            rows = session.execute(
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
            ).mappings()
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

    @staticmethod
    def _ensure_underlying(session: Session, symbol: str) -> int:
        normalized_symbol = symbol.upper()
        configured = ACTIVE_UNDERLYINGS_BY_SYMBOL.get(normalized_symbol)
        kind = configured.kind.value if configured is not None else UnderlyingKind.EQUITY.value
        is_priority = configured.is_priority if configured is not None else False
        conflict_action = (
            "kind = EXCLUDED.kind, is_priority = EXCLUDED.is_priority"
            if configured is not None
            else "symbol = EXCLUDED.symbol"
        )
        return session.execute(
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
        ).scalar_one()

    @staticmethod
    def _gamma_from_row(mapping: RowMapping) -> GammaAggregate:
        return GammaAggregate(
            symbol=str(mapping["symbol"]),
            as_of=mapping["time"],
            gamma_flip=Decimal(mapping["gamma_flip"]),
            call_wall=Decimal(mapping["call_wall"]),
            put_wall=Decimal(mapping["put_wall"]),
            max_pain=Decimal(mapping["max_pain"]),
            net_gamma=Decimal(mapping["net_gamma"]),
            dealer_gamma_notional=Decimal(mapping["dealer_gamma_notional"]),
            vega_exposure=Decimal(mapping["vega_exposure"]),
            theta_exposure=Decimal(mapping["theta_exposure"]),
            charm_exposure=Decimal(mapping["charm_exposure"]),
            vanna_exposure=Decimal(mapping["vanna_exposure"]),
            absolute_gamma_strike=Decimal(mapping["absolute_gamma_strike"]),
        )
