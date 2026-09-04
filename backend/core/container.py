from __future__ import annotations

import os
import socket
from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from backend.adapters.providers.mock import MockDataProvider
from backend.adapters.providers.mock.fake import FakeGreeksCalculator
from backend.adapters.providers.mock.gamma_aggregate import FakeGammaAggregateCalculator
from backend.adapters.providers.mock.gamma_exposure import FakeGammaExposureCalculator
from backend.adapters.providers.mock.gamma_flip import FakeGammaFlipCalculator
from backend.adapters.providers.mock.max_pain import FakeMaxPainCalculator
from backend.adapters.providers.mock.walls import FakeWallCalculator
from backend.adapters.providers.thetadata import ThetaDataProvider
from backend.adapters.providers.thetadata.provider import THETADATA_MAX_CONCURRENT_REQUESTS
from backend.adapters.providers.thetadata.request_slots import build_theta_request_slots
from backend.adapters.storage.memory import InMemoryStorage
from backend.adapters.storage.postgresql import PostgreSQLStorage
from backend.adapters.storage.postgresql_async import AsyncPostgreSQLStorage
from backend.adapters.storage.sync_read_adapter import SyncStorageAsyncReadAdapter
from backend.core.price_notifications import PriceNotificationHub, PriceNotificationListener
from backend.core.settings import Settings, get_settings
from backend.domain.ports import (
    IAsyncMarketReadStorage,
    IDataProvider,
    IGammaAggregateCalculator,
    IGammaExposureCalculator,
    IGammaFlipCalculator,
    IGreeksCalculator,
    IMaxPainCalculator,
    IStorage,
    IWallCalculator,
)
from backend.domain.use_cases import (
    CalculateDerivedMetricsUseCase,
    CalculateGammaAggregateUseCase,
    CalculateGammaExposureOrchestrator,
    CalculateGammaExposureUseCase,
    CalculateGammaFlipUseCase,
    CalculateGreeksUseCase,
    CalculateMaxPainUseCase,
    CalculateWallsUseCase,
    GetMarketSnapshotUseCase,
    LoadOptionChainUseCase,
    RefreshUnderlyingSnapshotUseCase,
    WhaleAlertsEngine,
)


@dataclass(frozen=True)
class Container:
    """Application dependency container.

    The container is intentionally minimal in this stage and only exposes
    infrastructure-level dependencies required to boot the API.
    """

    settings: Settings
    database_engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    storage_engine: Engine | None
    storage: IStorage
    async_market_storage: IAsyncMarketReadStorage
    market_data_provider: IDataProvider
    greeks_calculator: IGreeksCalculator
    gamma_exposure_calculator: IGammaExposureCalculator
    gamma_aggregate_calculator: IGammaAggregateCalculator
    gamma_flip_calculator: IGammaFlipCalculator
    wall_calculator: IWallCalculator
    max_pain_calculator: IMaxPainCalculator
    get_market_snapshot_use_case: GetMarketSnapshotUseCase
    load_option_chain_use_case: LoadOptionChainUseCase
    calculate_greeks_use_case: CalculateGreeksUseCase
    calculate_gamma_exposure_use_case: CalculateGammaExposureUseCase
    calculate_gamma_aggregate_use_case: CalculateGammaAggregateUseCase
    calculate_gamma_flip_use_case: CalculateGammaFlipUseCase
    calculate_walls_use_case: CalculateWallsUseCase
    calculate_max_pain_use_case: CalculateMaxPainUseCase
    calculate_gamma_exposure_orchestrator: CalculateGammaExposureOrchestrator
    calculate_derived_metrics_use_case: CalculateDerivedMetricsUseCase
    whale_alerts_engine: WhaleAlertsEngine
    refresh_underlying_snapshot_use_case: RefreshUnderlyingSnapshotUseCase
    price_notification_hub: PriceNotificationHub
    # None without a real Postgres behind DATABASE_URL (tests, sqlite) --
    # asyncpg.connect() has nothing to LISTEN on there. The hub above is
    # still always present so the WebSocket route never needs to branch
    # on which environment it's running in, only this.
    price_notification_listener: PriceNotificationListener | None


def build_whale_alerts_engine(storage: IStorage) -> WhaleAlertsEngine:
    """Build Whale Alerts, reading per-symbol threshold overrides live from storage."""
    return WhaleAlertsEngine(storage=storage)


def build_container() -> Container:
    settings = get_settings()
    from backend.infrastructure.database.engine import create_engine, create_sync_engine
    from backend.infrastructure.database.session import (
        create_session_factory,
        create_sync_session_factory,
    )

    database_engine = create_engine(settings.database_url, echo=settings.database_echo)
    session_factory = create_session_factory(database_engine)
    if settings.database_url.startswith("postgresql"):
        storage_engine = create_sync_engine(settings.database_url, echo=settings.database_echo)
        sync_session_factory = create_sync_session_factory(storage_engine)
        storage: IStorage = PostgreSQLStorage(sync_session_factory)
        # Real async Postgres reads for /gamma/{symbol} and /market/{symbol}
        # only when there's a real Postgres behind `session_factory` --
        # otherwise (InMemoryStorage, e.g. tests' sqlite DATABASE_URL, which
        # the async engine above can't share data with) fall back to
        # wrapping the same sync `storage` those routes already work
        # against, so both backends serve identical data either way.
        async_market_storage: IAsyncMarketReadStorage = AsyncPostgreSQLStorage(session_factory)
    else:
        storage_engine = None
        sync_session_factory = None
        storage = InMemoryStorage()
        async_market_storage = SyncStorageAsyncReadAdapter(storage)
    theta_request_slots = build_theta_request_slots(
        storage_engine,
        sync_session_factory,
        holder=f"{socket.gethostname()}:{os.getpid()}",
        limit=THETADATA_MAX_CONCURRENT_REQUESTS,
    )
    price_notification_hub = PriceNotificationHub()
    price_notification_listener = (
        PriceNotificationListener(settings.database_url, price_notification_hub)
        if settings.database_url.startswith("postgresql")
        else None
    )
    market_data_provider: IDataProvider = (
        ThetaDataProvider(
            settings.thetadata_rest_url, settings.thetadata_ws_url, theta_request_slots
        )
        if settings.data_provider == "thetadata"
        else MockDataProvider()
    )
    greeks_calculator = FakeGreeksCalculator()
    gamma_exposure_calculator = FakeGammaExposureCalculator()
    gamma_aggregate_calculator = FakeGammaAggregateCalculator()
    gamma_flip_calculator = FakeGammaFlipCalculator()
    wall_calculator = FakeWallCalculator()
    max_pain_calculator = FakeMaxPainCalculator()
    get_market_snapshot_use_case = GetMarketSnapshotUseCase(market_data_provider)
    load_option_chain_use_case = LoadOptionChainUseCase(market_data_provider)
    calculate_greeks_use_case = CalculateGreeksUseCase(greeks_calculator)
    calculate_gamma_exposure_use_case = CalculateGammaExposureUseCase(gamma_exposure_calculator)
    calculate_gamma_aggregate_use_case = CalculateGammaAggregateUseCase(
        gamma_exposure_calculator, gamma_aggregate_calculator
    )
    calculate_gamma_flip_use_case = CalculateGammaFlipUseCase(gamma_flip_calculator)
    calculate_walls_use_case = CalculateWallsUseCase(wall_calculator)
    calculate_max_pain_use_case = CalculateMaxPainUseCase(max_pain_calculator)
    calculate_gamma_exposure_orchestrator = CalculateGammaExposureOrchestrator(
        storage=storage,
        greeks=calculate_greeks_use_case,
        aggregate=calculate_gamma_aggregate_use_case,
        gamma_flip=calculate_gamma_flip_use_case,
        walls=calculate_walls_use_case,
        max_pain=calculate_max_pain_use_case,
    )
    calculate_derived_metrics_use_case = CalculateDerivedMetricsUseCase(storage)
    whale_alerts_engine = build_whale_alerts_engine(storage)
    refresh_underlying_snapshot_use_case = RefreshUnderlyingSnapshotUseCase(
        storage=storage,
        market_data_provider=market_data_provider,
        whale_alerts_engine=whale_alerts_engine,
        gamma_exposure_orchestrator=calculate_gamma_exposure_orchestrator,
        derived_metrics_use_case=calculate_derived_metrics_use_case,
    )
    return Container(
        settings=settings,
        database_engine=database_engine,
        session_factory=session_factory,
        storage_engine=storage_engine,
        storage=storage,
        async_market_storage=async_market_storage,
        market_data_provider=market_data_provider,
        greeks_calculator=greeks_calculator,
        gamma_exposure_calculator=gamma_exposure_calculator,
        gamma_aggregate_calculator=gamma_aggregate_calculator,
        gamma_flip_calculator=gamma_flip_calculator,
        wall_calculator=wall_calculator,
        max_pain_calculator=max_pain_calculator,
        get_market_snapshot_use_case=get_market_snapshot_use_case,
        load_option_chain_use_case=load_option_chain_use_case,
        calculate_greeks_use_case=calculate_greeks_use_case,
        calculate_gamma_exposure_use_case=calculate_gamma_exposure_use_case,
        calculate_gamma_aggregate_use_case=calculate_gamma_aggregate_use_case,
        calculate_gamma_flip_use_case=calculate_gamma_flip_use_case,
        calculate_walls_use_case=calculate_walls_use_case,
        calculate_max_pain_use_case=calculate_max_pain_use_case,
        calculate_gamma_exposure_orchestrator=calculate_gamma_exposure_orchestrator,
        calculate_derived_metrics_use_case=calculate_derived_metrics_use_case,
        whale_alerts_engine=whale_alerts_engine,
        refresh_underlying_snapshot_use_case=refresh_underlying_snapshot_use_case,
        price_notification_hub=price_notification_hub,
        price_notification_listener=price_notification_listener,
    )
