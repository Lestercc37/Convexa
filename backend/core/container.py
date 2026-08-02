from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from backend.adapters.providers.mock.fake import FakeGreeksCalculator
from backend.adapters.providers.mock.gamma_aggregate import FakeGammaAggregateCalculator
from backend.adapters.providers.mock.gamma_exposure import FakeGammaExposureCalculator
from backend.adapters.providers.mock.gamma_flip import FakeGammaFlipCalculator
from backend.adapters.providers.mock.max_pain import FakeMaxPainCalculator
from backend.adapters.providers.mock.walls import FakeWallCalculator
from backend.adapters.providers.mock import MockDataProvider
from backend.adapters.storage.postgresql import PostgreSQLStorage
from backend.domain.use_cases import (
    CalculateGammaAggregateUseCase,
    CalculateGammaExposureUseCase,
    CalculateGammaFlipUseCase,
    CalculateGreeksUseCase,
    CalculateMaxPainUseCase,
    CalculateWallsUseCase,
    GetMarketSnapshotUseCase,
    LoadOptionChainUseCase,
)
from backend.core.settings import Settings, get_settings
from backend.domain.ports import (
    IDataProvider,
    IGammaAggregateCalculator,
    IGammaExposureCalculator,
    IGammaFlipCalculator,
    IGreeksCalculator,
    IMaxPainCalculator,
    IWallCalculator,
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
    storage: PostgreSQLStorage
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


def build_container() -> Container:
    settings = get_settings()
    from backend.infrastructure.database.engine import create_engine
    from backend.infrastructure.database.session import create_session_factory

    database_engine = create_engine(settings.database_url, echo=settings.database_echo)
    session_factory = create_session_factory(database_engine)
    storage = PostgreSQLStorage(session_factory)
    market_data_provider = MockDataProvider()
    greeks_calculator = FakeGreeksCalculator()
    gamma_exposure_calculator = FakeGammaExposureCalculator()
    gamma_aggregate_calculator = FakeGammaAggregateCalculator()
    gamma_flip_calculator = FakeGammaFlipCalculator()
    wall_calculator = FakeWallCalculator()
    max_pain_calculator = FakeMaxPainCalculator()
    get_market_snapshot_use_case = GetMarketSnapshotUseCase(market_data_provider)
    load_option_chain_use_case = LoadOptionChainUseCase(market_data_provider)
    calculate_greeks_use_case = CalculateGreeksUseCase(greeks_calculator)
    calculate_gamma_exposure_use_case = CalculateGammaExposureUseCase(
        gamma_exposure_calculator
    )
    calculate_gamma_aggregate_use_case = CalculateGammaAggregateUseCase(
        gamma_exposure_calculator, gamma_aggregate_calculator
    )
    calculate_gamma_flip_use_case = CalculateGammaFlipUseCase(gamma_flip_calculator)
    calculate_walls_use_case = CalculateWallsUseCase(wall_calculator)
    calculate_max_pain_use_case = CalculateMaxPainUseCase(max_pain_calculator)
    return Container(
        settings=settings,
        database_engine=database_engine,
        session_factory=session_factory,
        storage=storage,
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
    )
