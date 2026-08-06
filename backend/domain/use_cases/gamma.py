from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from backend.domain.entities import GammaAggregate
from backend.domain.ports import IStorage
from backend.domain.use_cases.calculate_gamma_aggregate import (
    CalculateGammaAggregateUseCase,
)
from backend.domain.use_cases.calculate_gamma_flip import CalculateGammaFlipUseCase
from backend.domain.use_cases.calculate_greeks import CalculateGreeksUseCase
from backend.domain.use_cases.calculate_max_pain import CalculateMaxPainUseCase
from backend.domain.use_cases.calculate_walls import CalculateWallsUseCase
from backend.domain.use_cases.errors import NotFoundError


def get_gamma_exposure(storage: IStorage, underlying: str) -> GammaAggregate:
    gamma = storage.get_latest_gamma_aggregate(underlying)
    if gamma is None:
        raise NotFoundError(f"No gamma aggregate found for {underlying.upper()}")
    return gamma


def get_gamma_history(
    storage: IStorage, underlying: str, start: datetime, end: datetime
) -> list[GammaAggregate]:
    return storage.get_gamma_history(underlying, start, end)


class CalculateGammaExposureOrchestrator:
    """Build and persist the consolidated gamma result from a stored chain."""

    def __init__(
        self,
        storage: IStorage,
        greeks: CalculateGreeksUseCase,
        aggregate: CalculateGammaAggregateUseCase,
        gamma_flip: CalculateGammaFlipUseCase,
        walls: CalculateWallsUseCase,
        max_pain: CalculateMaxPainUseCase,
    ) -> None:
        self._storage = storage
        self._greeks = greeks
        self._aggregate = aggregate
        self._gamma_flip = gamma_flip
        self._walls = walls
        self._max_pain = max_pain

    def execute(self, underlying: str) -> GammaAggregate:
        chain = self._storage.get_latest_chain_snapshot(underlying)
        if chain is None:
            raise NotFoundError(f"No option chain found for {underlying.upper()}")

        enriched_chain = self._greeks.execute(chain)
        aggregate = self._aggregate.execute(enriched_chain)
        gamma_flip = self._gamma_flip.execute(aggregate)
        walls = self._walls.execute(aggregate)
        max_pain = self._max_pain.execute(enriched_chain)
        contract_multiplier = Decimal(100)
        vega_exposure = sum(
            (
                contract.greeks.vega * Decimal(contract.open_interest) * contract_multiplier
                for contract in enriched_chain.contracts
            ),
            Decimal(0),
        )
        theta_exposure = sum(
            (
                contract.greeks.theta * Decimal(contract.open_interest) * contract_multiplier
                for contract in enriched_chain.contracts
            ),
            Decimal(0),
        )
        charm_exposure = sum(
            (
                contract.greeks.charm * Decimal(contract.open_interest) * contract_multiplier
                for contract in enriched_chain.contracts
            ),
            Decimal(0),
        )
        vanna_exposure = sum(
            (
                contract.greeks.vanna
                * Decimal(contract.open_interest)
                * contract_multiplier
                * enriched_chain.spot_price
                for contract in enriched_chain.contracts
            ),
            Decimal(0),
        )

        result = replace(
            aggregate,
            gamma_flip=gamma_flip.gamma_flip_price or aggregate.gamma_flip,
            call_wall=(
                walls.call_wall.strike if walls.call_wall is not None else aggregate.call_wall
            ),
            put_wall=(walls.put_wall.strike if walls.put_wall is not None else aggregate.put_wall),
            max_pain=max_pain.max_pain_strike,
            vega_exposure=vega_exposure,
            theta_exposure=theta_exposure,
            charm_exposure=charm_exposure,
            vanna_exposure=vanna_exposure,
        )
        self._storage.save_gamma_aggregate(result)
        return result


def calculate_gamma_exposure(
    orchestrator: CalculateGammaExposureOrchestrator, underlying: str
) -> GammaAggregate:
    """Run the internal storage-backed gamma orchestration."""
    return orchestrator.execute(underlying)
