from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session, sessionmaker

from backend.domain.entities import (
    AggressorSide,
    ContractType,
    DailyBar,
    DailyGammaReference,
    ExposureLeadersSettings,
    FlowEvent,
    FlowEventType,
    GammaAggregate,
    GammaAggregateItem,
    Greeks,
    MarketPrice,
    MinuteBar,
    NegativeGammaBoardSettings,
    OptionChain,
    OptionContract,
    ScreenerPreset,
    ScreenerPresetSettings,
    Underlying,
    UnderlyingKind,
    WhaleThreshold,
)
from backend.domain.underlyings import ACTIVE_UNDERLYINGS_BY_SYMBOL
from backend.domain.use_cases.flow import SymbolFlowPressure, WhaleAlert, WhaleAlertType


class PostgreSQLStorage:
    """Synchronous PostgreSQL implementation of the domain storage port."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory
        # See _ensure_underlying's own comment -- a symbol's underlying_id
        # never changes once seeded, so this cache is safe to keep for the
        # whole process lifetime, not just per-call. Same fix already
        # applied to AsyncPostgreSQLStorage (2026-09-22) -- this class was
        # the one call path that never got it, confirmed live 2026-09-24
        # to be a real, measured source of lock contention on the tiny
        # (~15-row) underlyings table under real concurrent write load
        # (multiple whale-alerts consumer threads, the REST scheduler,
        # daily-bar/gamma-aggregate writers all hitting this at once).
        self._underlying_id_cache: dict[str, int] = {}

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
                        unusual_multiplier, whale_multiplier, sustained_flow_min
                    )
                    VALUES (
                        :underlying_id, :unusual_min, :whale_min,
                        :unusual_multiplier, :whale_multiplier, :sustained_flow_min
                    )
                    ON CONFLICT (underlying_id) DO UPDATE SET
                        unusual_min = EXCLUDED.unusual_min,
                        whale_min = EXCLUDED.whale_min,
                        unusual_multiplier = EXCLUDED.unusual_multiplier,
                        whale_multiplier = EXCLUDED.whale_multiplier,
                        sustained_flow_min = EXCLUDED.sustained_flow_min
                    """
                ),
                {
                    "underlying_id": underlying_id,
                    "unusual_min": str(threshold.unusual_min),
                    "whale_min": str(threshold.whale_min),
                    "unusual_multiplier": str(threshold.unusual_multiplier),
                    "whale_multiplier": str(threshold.whale_multiplier),
                    "sustained_flow_min": str(threshold.sustained_flow_min),
                },
            )

    def get_whale_thresholds(self) -> dict[str, WhaleThreshold]:
        with self.session_factory() as session:
            rows = session.execute(
                text(
                    """
                    SELECT u.symbol, w.unusual_min, w.whale_min,
                           w.unusual_multiplier, w.whale_multiplier, w.sustained_flow_min
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
                    sustained_flow_min=Decimal(str(row["sustained_flow_min"])),
                )
                for row in rows
            }

    def save_screener_preset_settings(
        self, preset: ScreenerPreset, settings: ScreenerPresetSettings
    ) -> None:
        parameters = self._screener_preset_settings_to_json(settings)
        with self.session_factory.begin() as session:
            session.execute(
                text(
                    """
                    INSERT INTO screener_preset_settings (preset, parameters)
                    VALUES (:preset, CAST(:parameters AS jsonb))
                    ON CONFLICT (preset) DO UPDATE SET
                        parameters = EXCLUDED.parameters
                    """
                ),
                {"preset": preset.value, "parameters": json.dumps(parameters)},
            )

    def get_screener_preset_settings(
        self, preset: ScreenerPreset
    ) -> ScreenerPresetSettings | None:
        with self.session_factory() as session:
            row = session.execute(
                text(
                    """
                    SELECT parameters FROM screener_preset_settings WHERE preset = :preset
                    """
                ),
                {"preset": preset.value},
            ).mappings().one_or_none()
        if row is None:
            return None
        raw_parameters = row["parameters"]
        # The JSONB driver normally deserializes to a dict already, but
        # fall back to parsing explicitly rather than assume one behavior.
        parameters: dict[str, str | int | None] = (
            json.loads(raw_parameters) if isinstance(raw_parameters, str) else raw_parameters
        )
        return self._screener_preset_settings_from_json(preset, parameters)

    @staticmethod
    def _screener_preset_settings_to_json(
        settings: ScreenerPresetSettings,
    ) -> dict[str, str | int | None]:
        if isinstance(settings, NegativeGammaBoardSettings):
            return {"net_gamma_max": str(settings.net_gamma_max)}
        return {
            "min_magnitude": (
                str(settings.min_magnitude) if settings.min_magnitude is not None else None
            ),
            "limit": settings.limit,
        }

    @staticmethod
    def _screener_preset_settings_from_json(
        preset: ScreenerPreset, parameters: dict[str, str | int | None]
    ) -> ScreenerPresetSettings:
        if preset is ScreenerPreset.NEGATIVE_GAMMA_BOARD:
            return NegativeGammaBoardSettings(
                net_gamma_max=Decimal(str(parameters["net_gamma_max"]))
            )
        min_magnitude = parameters.get("min_magnitude")
        limit = parameters.get("limit")
        return ExposureLeadersSettings(
            min_magnitude=Decimal(str(min_magnitude)) if min_magnitude is not None else None,
            limit=int(limit) if limit is not None else None,
        )

    def save_chain_snapshot(self, chain: OptionChain) -> None:
        # Chunked multi-row upserts, not one contract at a time -- confirmed
        # live, 2026-09-22: the old row-by-row loop issued two sequential
        # INSERT round-trips per contract inside ONE transaction, so SPX
        # alone (~8,000 contracts after PR #159 widened the persisted
        # strike range) meant ~16,000 sequential round-trips held open
        # under a single `session_factory.begin()`. With 15 symbols
        # writing concurrently via the scheduler, this produced a real
        # Postgres pile-up (40 connections, 16 waiting on a lock) even on
        # a clean restart with no orphaned session involved -- confirmed
        # via pg_locks that it was genuine write-path slowness, not a
        # deadlock (it drained on its own). CHUNK_SIZE keeps each
        # statement's bound-parameter count (5 or 15 per row) well under
        # PostgreSQL's ~65,535-per-statement protocol limit while still
        # cutting round-trips by ~2 orders of magnitude.
        if not chain.contracts:
            return
        CHUNK_SIZE = 500
        with self.session_factory.begin() as session:
            underlying_id = self._ensure_underlying(session, chain.symbol)
            contracts = chain.contracts
            for start in range(0, len(contracts), CHUNK_SIZE):
                batch = contracts[start : start + CHUNK_SIZE]

                contract_values_sql = ", ".join(
                    f"(:underlying_id_{i}, :strike_{i}, :expiration_{i}, "
                    f":contract_type_{i}, :occ_symbol_{i})"
                    for i in range(len(batch))
                )
                contract_params: dict[str, object] = {}
                for i, contract in enumerate(batch):
                    contract_params[f"underlying_id_{i}"] = underlying_id
                    contract_params[f"strike_{i}"] = contract.strike
                    contract_params[f"expiration_{i}"] = contract.expiration
                    contract_params[f"contract_type_{i}"] = contract.contract_type.value
                    contract_params[f"occ_symbol_{i}"] = contract.occ_symbol
                contract_rows = session.execute(
                    text(
                        f"""
                        INSERT INTO option_contracts (
                            underlying_id, strike, expiration, contract_type, occ_symbol
                        )
                        VALUES {contract_values_sql}
                        ON CONFLICT (occ_symbol) DO UPDATE SET
                            underlying_id = EXCLUDED.underlying_id,
                            strike = EXCLUDED.strike,
                            expiration = EXCLUDED.expiration,
                            contract_type = EXCLUDED.contract_type
                        RETURNING id, occ_symbol
                        """
                    ),
                    contract_params,
                ).all()
                contract_id_by_occ_symbol = {row.occ_symbol: row.id for row in contract_rows}

                snapshot_values_sql = ", ".join(
                    f"(:time_{i}, :contract_id_{i}, :bid_{i}, :ask_{i}, :last_{i}, :volume_{i}, "
                    f":open_interest_{i}, :iv_{i}, :delta_{i}, :gamma_{i}, :theta_{i}, :vega_{i}, "
                    f":charm_{i}, :vanna_{i}, :spot_price_{i})"
                    for i in range(len(batch))
                )
                snapshot_params: dict[str, object] = {}
                for i, contract in enumerate(batch):
                    snapshot_params[f"time_{i}"] = chain.as_of
                    snapshot_params[f"contract_id_{i}"] = contract_id_by_occ_symbol[
                        contract.occ_symbol
                    ]
                    snapshot_params[f"bid_{i}"] = contract.bid
                    snapshot_params[f"ask_{i}"] = contract.ask
                    snapshot_params[f"last_{i}"] = contract.last
                    snapshot_params[f"volume_{i}"] = contract.volume
                    snapshot_params[f"open_interest_{i}"] = contract.open_interest
                    snapshot_params[f"iv_{i}"] = contract.iv
                    snapshot_params[f"delta_{i}"] = contract.greeks.delta
                    snapshot_params[f"gamma_{i}"] = contract.greeks.gamma
                    snapshot_params[f"theta_{i}"] = contract.greeks.theta
                    snapshot_params[f"vega_{i}"] = contract.greeks.vega
                    snapshot_params[f"charm_{i}"] = contract.greeks.charm
                    snapshot_params[f"vanna_{i}"] = contract.greeks.vanna
                    snapshot_params[f"spot_price_{i}"] = chain.spot_price
                session.execute(
                    text(
                        f"""
                        INSERT INTO option_chain_snapshots (
                            time, contract_id, bid, ask, last, volume,
                            open_interest, iv, delta, gamma, theta, vega,
                            charm, vanna, spot_price
                        )
                        VALUES {snapshot_values_sql}
                        """
                    ),
                    snapshot_params,
                )

    def get_latest_chain_snapshot(
        self, underlying: str, expiration: date | None = None
    ) -> OptionChain | None:
        expiration_filter = "AND oc.expiration = :expiration" if expiration else ""
        # An unscoped ("give me whatever's latest") read can collide with
        # two structurally different write shapes sharing this table: the
        # scheduler's full multi-expiration fetch (Gamma Aggregate's real
        # input, CalculateGammaExposureOrchestrator) and a narrow single-
        # expiration fetch from anything that calls this storage with an
        # explicit `expiration` (LoadOptionChainUseCase, and
        # read_models.get_option_chain's on-demand /chain/{symbol} refresh
        # -- see the `expiration_filter` branch below, which this guard
        # never touches). If the narrow write lands with a fresher `time`
        # than the last full one, a plain MAX(s.time) would silently hand
        # the orchestrator a tiny, unrepresentative slice of the book
        # instead of the full chain -- confirmed live, 2026-09-18: SPX
        # showing price above gamma_flip while still reporting short_gamma
        # with net_gamma in the -20B to -32B range, and ~1-in-5 cycles
        # collapsing to gamma_flip=null/walls=0 when that slice also had
        # degenerate IV. That fix only guarded indices, on the assumption
        # this race "only exists for indices in the first place" -- proven
        # wrong live, 2026-09-24: AAPL hit the identical symptom (gamma_
        # flip/walls silently going null/0 roughly every other scheduler
        # cycle) once the frontend's Volatility Smile panel was left open
        # on a stale, already-expired `selectedExpiration`, repeatedly
        # writing a 1-expiration/16-contract narrow snapshot that kept
        # winning the latest-`time` race against the scheduler's own
        # 25-expiration/~680-contract full write. Any symbol with more
        # than one real expiration (every actively traded US-listed
        # underlying this system tracks) can hit this the same way, so the
        # guard now applies unconditionally whenever the read is unscoped
        # -- not just for indices.
        if expiration is None:
            # Prefer a multi-expiration write, but never end up with
            # nothing just because one hasn't landed yet -- a brand-new
            # symbol before the scheduler's first full fetch, or an
            # environment that only ever wrote a single-expiration chain,
            # must still get that chain back rather than None. Ranks
            # candidates instead of filtering them out: priority 0 (a real
            # multi-expiration write) always wins over priority 1 (any
            # write at all) regardless of which is more recent, and only
            # falls back to priority 1 when no priority-0 row exists.
            latest_cte_sql = """
                WITH candidates AS (
                    SELECT s.time, 0 AS priority
                    FROM option_chain_snapshots AS s
                    JOIN option_contracts AS oc ON oc.id = s.contract_id
                    JOIN underlyings AS u ON u.id = oc.underlying_id
                    WHERE u.symbol = :symbol
                    GROUP BY s.time
                    HAVING COUNT(DISTINCT oc.expiration) > 1
                    UNION ALL
                    SELECT s.time, 1 AS priority
                    FROM option_chain_snapshots AS s
                    JOIN option_contracts AS oc ON oc.id = s.contract_id
                    JOIN underlyings AS u ON u.id = oc.underlying_id
                    WHERE u.symbol = :symbol
                    GROUP BY s.time
                ),
                latest AS (
                    SELECT time FROM candidates ORDER BY priority ASC, time DESC LIMIT 1
                )
            """
        else:
            latest_cte_sql = f"""
                WITH latest AS (
                    SELECT s.time
                    FROM option_chain_snapshots AS s
                    JOIN option_contracts AS oc ON oc.id = s.contract_id
                    JOIN underlyings AS u ON u.id = oc.underlying_id
                    WHERE u.symbol = :symbol
                    {expiration_filter}
                    GROUP BY s.time
                    ORDER BY s.time DESC
                    LIMIT 1
                )
            """
        statement = text(
            f"""
            {latest_cte_sql}
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
                        time, underlying_id, view, gamma_flip, call_wall, put_wall,
                        max_pain, net_gamma, dealer_gamma_notional,
                        vega_exposure, theta_exposure, charm_exposure,
                        vanna_exposure, delta_exposure,
                        absolute_gamma_strike,
                        total_market_gamma, positive_gamma, negative_gamma,
                        peak_gamma_value
                    )
                    VALUES (
                        :time, :underlying_id, :view, :gamma_flip, :call_wall, :put_wall,
                        :max_pain, :net_gamma, :dealer_gamma_notional,
                        :vega_exposure, :theta_exposure, :charm_exposure,
                        :vanna_exposure, :delta_exposure,
                        :absolute_gamma_strike,
                        :total_market_gamma, :positive_gamma, :negative_gamma,
                        :peak_gamma_value
                    )
                    ON CONFLICT (underlying_id, time, view) DO UPDATE SET
                        gamma_flip = EXCLUDED.gamma_flip,
                        call_wall = EXCLUDED.call_wall,
                        put_wall = EXCLUDED.put_wall,
                        max_pain = EXCLUDED.max_pain,
                        net_gamma = EXCLUDED.net_gamma,
                        dealer_gamma_notional = EXCLUDED.dealer_gamma_notional,
                        vega_exposure = EXCLUDED.vega_exposure,
                        theta_exposure = EXCLUDED.theta_exposure,
                        charm_exposure = EXCLUDED.charm_exposure,
                        vanna_exposure = EXCLUDED.vanna_exposure,
                        delta_exposure = EXCLUDED.delta_exposure,
                        absolute_gamma_strike = EXCLUDED.absolute_gamma_strike,
                        total_market_gamma = EXCLUDED.total_market_gamma,
                        positive_gamma = EXCLUDED.positive_gamma,
                        negative_gamma = EXCLUDED.negative_gamma,
                        peak_gamma_value = EXCLUDED.peak_gamma_value
                    """
                ),
                {
                    "time": gamma.as_of,
                    "underlying_id": underlying_id,
                    "view": gamma.view,
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
                    "delta_exposure": gamma.delta_exposure,
                    "absolute_gamma_strike": gamma.absolute_gamma_strike,
                    "total_market_gamma": gamma.total_market_gamma,
                    "positive_gamma": gamma.positive_gamma,
                    "negative_gamma": gamma.negative_gamma,
                    "peak_gamma_value": gamma.peak_gamma_value,
                },
            )
            for item in gamma.items:
                session.execute(
                    text(
                        """
                        INSERT INTO gamma_aggregate_items (
                            underlying_id, time, view, strike, total_gamma_exposure,
                            call_gamma_exposure, put_gamma_exposure, net_gamma,
                            contract_count, absolute_gamma, open_interest, volume
                        )
                        VALUES (
                            :underlying_id, :time, :view, :strike, :total_gamma_exposure,
                            :call_gamma_exposure, :put_gamma_exposure, :net_gamma,
                            :contract_count, :absolute_gamma, :open_interest, :volume
                        )
                        ON CONFLICT (underlying_id, time, view, strike) DO UPDATE SET
                            total_gamma_exposure = EXCLUDED.total_gamma_exposure,
                            call_gamma_exposure = EXCLUDED.call_gamma_exposure,
                            put_gamma_exposure = EXCLUDED.put_gamma_exposure,
                            net_gamma = EXCLUDED.net_gamma,
                            contract_count = EXCLUDED.contract_count,
                            absolute_gamma = EXCLUDED.absolute_gamma,
                            open_interest = EXCLUDED.open_interest,
                            volume = EXCLUDED.volume
                        """
                    ),
                    {
                        "underlying_id": underlying_id,
                        "time": gamma.as_of,
                        "view": gamma.view,
                        "strike": item.strike,
                        "total_gamma_exposure": item.total_gamma_exposure,
                        "call_gamma_exposure": item.call_gamma_exposure,
                        "put_gamma_exposure": item.put_gamma_exposure,
                        "net_gamma": item.net_gamma,
                        "contract_count": item.contract_count,
                        "absolute_gamma": item.absolute_gamma,
                        "open_interest": item.open_interest,
                        "volume": item.volume,
                    },
                )

    def get_latest_gamma_aggregate(
        self, underlying: str, view: str = "structural"
    ) -> GammaAggregate | None:
        with self.session_factory() as session:
            row = (
                session.execute(
                    text(
                        """
                    SELECT g.time, g.underlying_id, u.symbol, g.view, g.gamma_flip, g.call_wall,
                           g.put_wall, g.max_pain, g.net_gamma,
                           g.dealer_gamma_notional, g.vega_exposure,
                           g.theta_exposure, g.charm_exposure,
                           g.vanna_exposure, g.delta_exposure,
                           g.absolute_gamma_strike,
                           g.total_market_gamma, g.positive_gamma, g.negative_gamma,
                           g.peak_gamma_value
                    FROM gamma_aggregates AS g
                    JOIN underlyings AS u ON u.id = g.underlying_id
                    WHERE u.symbol = :symbol AND g.view = :view
                    ORDER BY g.time DESC
                    LIMIT 1
                    """
                    ),
                    {"symbol": underlying.upper(), "view": view},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            items = self._gamma_aggregate_items(session, row["underlying_id"], row["time"], view)
        return replace(self._gamma_from_row(row), items=items)

    def _gamma_aggregate_items(
        self, session: Session, underlying_id: int, time: datetime, view: str = "structural"
    ) -> tuple[GammaAggregateItem, ...]:
        rows = session.execute(
            text(
                """
                SELECT strike, total_gamma_exposure, call_gamma_exposure,
                       put_gamma_exposure, net_gamma, contract_count,
                       absolute_gamma, open_interest, volume
                FROM gamma_aggregate_items
                WHERE underlying_id = :underlying_id AND time = :time AND view = :view
                ORDER BY strike
                """
            ),
            {"underlying_id": underlying_id, "time": time, "view": view},
        ).mappings()
        return tuple(
            GammaAggregateItem(
                strike=Decimal(row["strike"]),
                total_gamma_exposure=Decimal(row["total_gamma_exposure"]),
                call_gamma_exposure=Decimal(row["call_gamma_exposure"]),
                put_gamma_exposure=Decimal(row["put_gamma_exposure"]),
                net_gamma=Decimal(row["net_gamma"]),
                contract_count=int(row["contract_count"]),
                absolute_gamma=Decimal(row["absolute_gamma"]),
                open_interest=int(row["open_interest"]),
                volume=int(row["volume"]),
            )
            for row in rows
        )

    def get_gamma_history(
        self, underlying: str, start: datetime, end: datetime, view: str = "structural"
    ) -> list[GammaAggregate]:
        with self.session_factory() as session:
            rows = session.execute(
                text(
                    """
                    SELECT g.time, u.symbol, g.view, g.gamma_flip, g.call_wall,
                           g.put_wall, g.max_pain, g.net_gamma,
                           g.dealer_gamma_notional, g.vega_exposure,
                           g.theta_exposure, g.charm_exposure,
                           g.vanna_exposure, g.delta_exposure,
                           g.absolute_gamma_strike,
                           g.total_market_gamma, g.positive_gamma, g.negative_gamma,
                           g.peak_gamma_value
                    FROM gamma_aggregates AS g
                    JOIN underlyings AS u ON u.id = g.underlying_id
                    WHERE u.symbol = :symbol AND g.view = :view
                      AND g.time BETWEEN :start AND :end
                    ORDER BY g.time
                    """
                ),
                {"symbol": underlying.upper(), "start": start, "end": end, "view": view},
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

    def save_whale_alert(self, alert: WhaleAlert) -> None:
        with self.session_factory.begin() as session:
            underlying_id = self._ensure_underlying(session, alert.symbol)
            session.execute(
                text(
                    """
                    INSERT INTO whale_alerts (
                        time, underlying_id, occ_symbol, alert_type, amount,
                        estimated_buy_volume, estimated_sell_volume, quote_unavailable
                    )
                    VALUES (
                        :time, :underlying_id, :occ_symbol, :alert_type, :amount,
                        :estimated_buy_volume, :estimated_sell_volume, :quote_unavailable
                    )
                    """
                ),
                {
                    "time": alert.as_of,
                    "underlying_id": underlying_id,
                    "occ_symbol": alert.occ_symbol,
                    "alert_type": alert.alert_type.value,
                    "amount": alert.amount,
                    "estimated_buy_volume": alert.estimated_buy_volume,
                    "estimated_sell_volume": alert.estimated_sell_volume,
                    "quote_unavailable": alert.quote_unavailable,
                },
            )

    def get_recent_whale_alerts(self, underlying: str, limit: int = 100) -> list[WhaleAlert]:
        with self.session_factory() as session:
            rows = session.execute(
                text(
                    """
                    SELECT w.time, u.symbol, w.occ_symbol, w.alert_type, w.amount,
                           w.estimated_buy_volume, w.estimated_sell_volume, w.quote_unavailable
                    FROM whale_alerts AS w
                    JOIN underlyings AS u ON u.id = w.underlying_id
                    WHERE u.symbol = :symbol
                    ORDER BY w.time DESC
                    LIMIT :limit
                    """
                ),
                {"symbol": underlying.upper(), "limit": limit},
            ).mappings()
            return [
                WhaleAlert(
                    symbol=str(row["symbol"]),
                    occ_symbol=str(row["occ_symbol"]),
                    alert_type=WhaleAlertType(str(row["alert_type"])),
                    amount=Decimal(row["amount"]),
                    as_of=row["time"],
                    estimated_buy_volume=Decimal(row["estimated_buy_volume"]),
                    estimated_sell_volume=Decimal(row["estimated_sell_volume"]),
                    quote_unavailable=bool(row["quote_unavailable"]),
                )
                for row in rows
            ]

    def save_symbol_flow_pressure(self, flow: SymbolFlowPressure) -> None:
        # Upsert, not append: only the current snapshot is kept (unlike
        # whale_alerts' append-only history) -- the API only ever needs
        # "what is it right now", and the Worker overwrites this every
        # scheduler cycle (~30s) from WhaleAlertsEngine.symbol_flow()'s
        # own in-memory session accumulation, so there's no history to
        # lose by replacing the row each time.
        with self.session_factory.begin() as session:
            underlying_id = self._ensure_underlying(session, flow.symbol)
            session.execute(
                text(
                    """
                    INSERT INTO symbol_flow_pressure (
                        underlying_id, as_of, net_call_premium, net_put_premium,
                        rolling_net_call_premium, rolling_net_put_premium,
                        rolling_window_minutes
                    )
                    VALUES (
                        :underlying_id, :as_of, :net_call_premium, :net_put_premium,
                        :rolling_net_call_premium, :rolling_net_put_premium,
                        :rolling_window_minutes
                    )
                    ON CONFLICT (underlying_id) DO UPDATE SET
                        as_of = EXCLUDED.as_of,
                        net_call_premium = EXCLUDED.net_call_premium,
                        net_put_premium = EXCLUDED.net_put_premium,
                        rolling_net_call_premium = EXCLUDED.rolling_net_call_premium,
                        rolling_net_put_premium = EXCLUDED.rolling_net_put_premium,
                        rolling_window_minutes = EXCLUDED.rolling_window_minutes
                    """
                ),
                {
                    "underlying_id": underlying_id,
                    "as_of": flow.as_of,
                    "net_call_premium": flow.net_call_premium,
                    "net_put_premium": flow.net_put_premium,
                    "rolling_net_call_premium": flow.rolling_net_call_premium,
                    "rolling_net_put_premium": flow.rolling_net_put_premium,
                    "rolling_window_minutes": flow.rolling_window_minutes,
                },
            )

    def get_symbol_flow_pressure(self, underlying: str) -> SymbolFlowPressure | None:
        with self.session_factory() as session:
            row = (
                session.execute(
                    text(
                        """
                        SELECT u.symbol, f.as_of, f.net_call_premium, f.net_put_premium,
                               f.rolling_net_call_premium, f.rolling_net_put_premium,
                               f.rolling_window_minutes
                        FROM symbol_flow_pressure AS f
                        JOIN underlyings AS u ON u.id = f.underlying_id
                        WHERE u.symbol = :symbol
                        """
                    ),
                    {"symbol": underlying.upper()},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        net_call = Decimal(row["net_call_premium"])
        net_put = Decimal(row["net_put_premium"])
        rolling_call = Decimal(row["rolling_net_call_premium"])
        rolling_put = Decimal(row["rolling_net_put_premium"])
        return SymbolFlowPressure(
            symbol=str(row["symbol"]),
            as_of=row["as_of"],
            net_call_premium=net_call,
            net_put_premium=net_put,
            net_client_flow_pressure=net_call - net_put,
            rolling_net_call_premium=rolling_call,
            rolling_net_put_premium=rolling_put,
            rolling_net_client_flow_pressure=rolling_call - rolling_put,
            rolling_window_minutes=int(row["rolling_window_minutes"]),
        )

    def get_cumulative_volumes(self, occ_symbols: list[str]) -> dict[str, int]:
        """Bulk read for RefreshUnderlyingSnapshotUseCase's own volume
        merge -- see that use case's own comment. Keyed by occ_symbol
        (not contract_id), matching ThetaStreamHub's own in-memory
        _cumulative_volume dict exactly, so the merge is a plain dict
        lookup on the caller's side. A single ANY(:occ_symbols) query,
        not one row at a time -- a symbol's chain can have thousands of
        contracts (SPX/NDX especially), and this already runs inside
        the scheduler's own asyncio.to_thread per-symbol call, so there
        is no reason to pay per-contract round-trips here either."""
        if not occ_symbols:
            return {}
        with self.session_factory() as session:
            rows = session.execute(
                text(
                    """
                    SELECT occ_symbol, volume
                    FROM contract_cumulative_volume
                    WHERE occ_symbol = ANY(:occ_symbols)
                    """
                ),
                {"occ_symbols": occ_symbols},
            ).mappings()
            return {str(row["occ_symbol"]): int(row["volume"]) for row in rows}

    def save_cumulative_volumes(self, volumes: dict[str, int]) -> None:
        """Written periodically by whichever process actually owns a
        live ThetaData trade stream (ThetaStreamHub's own
        _cumulative_volume, see core/stream_state_export.py's export
        task) -- the ONLY source of truth for real cumulative volume.
        Lets a process without a live stream (the scheduler-only
        process, split out to stop it contending with ThetaStreamHub's
        own event loop for the GIL -- see get_cumulative_volumes' own
        comment) still report real volume instead of silently 0 for
        every contract. Chunked the same way save_chain_snapshot's own
        multi-row upsert is (500 rows per statement, well under
        Postgres's ~65,535-parameter-per-statement limit) -- this can be
        called with every contract this process has ever seen a trade
        for, easily thousands across 15 symbols."""
        if not volumes:
            return
        items = list(volumes.items())
        CHUNK_SIZE = 500
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            for start in range(0, len(items), CHUNK_SIZE):
                batch = items[start : start + CHUNK_SIZE]
                values_sql = ", ".join(
                    f"(:occ_symbol_{i}, :volume_{i}, :updated_at_{i})" for i in range(len(batch))
                )
                params: dict[str, object] = {}
                for i, (occ_symbol, volume) in enumerate(batch):
                    params[f"occ_symbol_{i}"] = occ_symbol
                    params[f"volume_{i}"] = volume
                    params[f"updated_at_{i}"] = now
                session.execute(
                    text(
                        f"""
                        INSERT INTO contract_cumulative_volume (occ_symbol, volume, updated_at)
                        VALUES {values_sql}
                        ON CONFLICT (occ_symbol) DO UPDATE SET
                            volume = EXCLUDED.volume,
                            updated_at = EXCLUDED.updated_at
                        """
                    ),
                    params,
                )

    def save_minute_bar(self, bar: MinuteBar) -> None:
        """Not part of `IStorage` — used only by the one-time Indices Pro
        backfill (`backend/scripts/backfill_minute_history.py`), not by
        any live use case yet."""
        with self.session_factory.begin() as session:
            underlying_id = self._ensure_underlying(session, bar.symbol)
            session.execute(
                text(
                    """
                    INSERT INTO minute_bars (
                        time, underlying_id, open, high, low, close, volume
                    )
                    VALUES (
                        :time, :underlying_id, :open, :high, :low, :close, :volume
                    )
                    ON CONFLICT (underlying_id, time) DO UPDATE SET
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        close = EXCLUDED.close,
                        volume = EXCLUDED.volume
                    """
                ),
                {
                    "time": bar.time,
                    "underlying_id": underlying_id,
                    "open": bar.open_price,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                },
            )

    def _ensure_underlying(self, session: Session, symbol: str) -> int:
        # Confirmed live, 2026-09-24: this UPSERT used to run
        # unconditionally on *every* call -- save_whale_alert alone can
        # fire from up to 15 concurrent worker threads (the dedicated
        # whale-alerts executor, one per active symbol), each hitting
        # this same tiny (~15-row) table independently, on top of the
        # REST scheduler's and every other writer's own calls through
        # this same method. Caught mid-incident: a real INSERT here blocked
        # for 11+ seconds on a row lock, tying up a worker thread that
        # should have taken microseconds -- directly behind the queue
        # backlogs/drops this session was investigating. An underlying's
        # id/kind/is_priority never changes once seeded (kind/is_priority
        # come from the static ACTIVE_UNDERLYINGS_BY_SYMBOL config, not
        # live data) -- caching the id for this process's lifetime turns
        # every call after the first one for a given symbol into a plain
        # dict lookup instead of a real INSERT..ON CONFLICT..RETURNING
        # round-trip. Same fix already applied to AsyncPostgreSQLStorage's
        # own _ensure_underlying (2026-09-22); this class was the one
        # call path that never got it.
        #
        # Thread safety: no lock around the cache. Two threads racing to
        # populate the same symbol's entry both perform the (idempotent)
        # UPSERT and both get the identical id back -- a wasted extra
        # round-trip in that rare case, never a wrong value, so a plain
        # dict is sufficient (CPython's GIL already makes the individual
        # get/set operations atomic; no torn reads/writes are possible).
        normalized_symbol = symbol.upper()
        cached = self._underlying_id_cache.get(normalized_symbol)
        if cached is not None:
            return cached
        configured = ACTIVE_UNDERLYINGS_BY_SYMBOL.get(normalized_symbol)
        kind = configured.kind.value if configured is not None else UnderlyingKind.EQUITY.value
        is_priority = configured.is_priority if configured is not None else False
        conflict_action = (
            "kind = EXCLUDED.kind, is_priority = EXCLUDED.is_priority"
            if configured is not None
            else "symbol = EXCLUDED.symbol"
        )
        underlying_id = session.execute(
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
        self._underlying_id_cache[normalized_symbol] = underlying_id
        return underlying_id

    @staticmethod
    def _gamma_from_row(mapping: RowMapping) -> GammaAggregate:
        return GammaAggregate(
            symbol=str(mapping["symbol"]),
            as_of=mapping["time"],
            view=str(mapping["view"]),
            # NULL means "no sign crossing found"/"no valid wall
            # candidate" -- a real, distinct outcome from a value of 0
            # (see GammaAggregate's own field comments). Every other
            # field here stays required.
            gamma_flip=(
                Decimal(mapping["gamma_flip"]) if mapping["gamma_flip"] is not None else None
            ),
            call_wall=(
                Decimal(mapping["call_wall"]) if mapping["call_wall"] is not None else None
            ),
            put_wall=(Decimal(mapping["put_wall"]) if mapping["put_wall"] is not None else None),
            max_pain=Decimal(mapping["max_pain"]),
            net_gamma=Decimal(mapping["net_gamma"]),
            dealer_gamma_notional=Decimal(mapping["dealer_gamma_notional"]),
            vega_exposure=Decimal(mapping["vega_exposure"]),
            theta_exposure=Decimal(mapping["theta_exposure"]),
            charm_exposure=Decimal(mapping["charm_exposure"]),
            vanna_exposure=Decimal(mapping["vanna_exposure"]),
            delta_exposure=Decimal(mapping["delta_exposure"]),
            absolute_gamma_strike=Decimal(mapping["absolute_gamma_strike"]),
            total_market_gamma=Decimal(mapping["total_market_gamma"]),
            positive_gamma=Decimal(mapping["positive_gamma"]),
            negative_gamma=Decimal(mapping["negative_gamma"]),
            peak_gamma_value=Decimal(mapping["peak_gamma_value"]),
        )
