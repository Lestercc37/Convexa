from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

from backend.domain.entities import GammaAggregate, OptionChain
from backend.domain.ports import IAsyncMarketReadStorage, IStorage
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


async def get_gamma_exposure_async(
    storage: IAsyncMarketReadStorage, underlying: str
) -> GammaAggregate:
    gamma = await storage.get_latest_gamma_aggregate(underlying)
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
        gamma_flip = self._gamma_flip.execute(aggregate, enriched_chain.spot_price)
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
        # No call/put sign flip, same as Vega/Theta/Charm/Vanna above --
        # unlike dealer_gamma_exposure's own +1 call/-1 put convention
        # (FakeGammaExposureCalculator, a separate calculation feeding
        # only Max Pain/Absolute Gamma/Walls), delta already carries its
        # own real sign here (a genuine ThetaData quote, not BSM-derived
        # like gamma/vanna/charm -- see calculate_bsm_greeks.py's own
        # docstring on which greeks are quoted vs. computed): positive
        # for calls, negative for puts. Summing it raw already nets
        # long/short directional exposure correctly, with no extra
        # convention to invent.
        delta_exposure = sum(
            (
                contract.greeks.delta * Decimal(contract.open_interest) * contract_multiplier
                for contract in enriched_chain.contracts
            ),
            Decimal(0),
        )

        result = replace(
            aggregate,
            # Not `gamma_flip.gamma_flip_price or aggregate.gamma_flip` --
            # that `or` collapsed a legitimate "no sign crossing found"
            # (gamma_flip_price is None, flip_found=False) into whatever
            # aggregate.gamma_flip already was (its own dataclass default,
            # None too) as if it meant something -- but more importantly,
            # `or` also treats a real crossing found exactly at 0 as falsy
            # and would have discarded that too. gamma_flip_price is
            # already the right value (None or a real Decimal) -- just use
            # it directly, no fallback needed.
            gamma_flip=gamma_flip.gamma_flip_price,
            call_wall=(
                walls.call_wall.strike if walls.call_wall is not None else aggregate.call_wall
            ),
            put_wall=(walls.put_wall.strike if walls.put_wall is not None else aggregate.put_wall),
            max_pain=max_pain.max_pain_strike,
            vega_exposure=vega_exposure,
            theta_exposure=theta_exposure,
            charm_exposure=charm_exposure,
            vanna_exposure=vanna_exposure,
            delta_exposure=delta_exposure,
        )
        self._storage.save_gamma_aggregate(result)
        return result


def calculate_gamma_exposure(
    orchestrator: CalculateGammaExposureOrchestrator, underlying: str
) -> GammaAggregate:
    """Run the internal storage-backed gamma orchestration."""
    return orchestrator.execute(underlying)


# P-E fix (2026-09-21): GammaAggregateItem collapses every contract to
# (strike) alone, discarding expiration -- reasonable when only one
# expiration was ever in view, but after the P1 rollout widened every
# symbol's own fetch to every expiration, a single far-dated outlier
# listing can drag GammaAggregateItem's own strike range far wider than
# what's actually relevant to near-term gamma structure. Confirmed live:
# SPX's book is uniformly [7665, 7855] across every one of its 57 near-term
# expirations (0-452 DTE) -- the sole exception is a single 2031 LEAPS
# listing (1,915 DTE) contributing just 2 strikes (7200, 8000) with real,
# if comparatively small, open interest, which alone doubled the
# GEX-by-strike chart's visible x-axis range (confirmed: filtering by
# open_interest > 0 alone does NOT exclude these -- open interest doesn't
# separate "near-term relevant" from "far-dated outlier", strike 8000 (OI
# 15,501) outranks several genuinely near-term strikes). AAPL and NDX both
# stay tight across their own full expiration range (851 and 1,187 DTE
# respectively) -- this isn't a systemic per-symbol tuning problem, just
# this one kind of rare far-outlier listing, so 30 days comfortably covers
# every real near-term expiration while excluding it.
NEAR_TERM_GAMMA_PROFILE_WINDOW_DAYS = 30


def _filter_to_near_term_expirations(chain: OptionChain, window_days: int) -> OptionChain:
    if not chain.contracts:
        return chain
    nearest_expiration = min(contract.expiration for contract in chain.contracts)
    cutoff = nearest_expiration + timedelta(days=window_days)
    near_term_contracts = tuple(
        contract for contract in chain.contracts if contract.expiration <= cutoff
    )
    return replace(chain, contracts=near_term_contracts)


class CalculateNearTermGammaProfileUseCase:
    """Gamma Aggregate limited to near-term expirations only -- built
    specifically for the GEX-by-strike chart (P-E), which needs a
    per-strike breakdown that isn't stretched by whatever far-dated
    outlier expiration a symbol happens to also list. See
    _filter_to_near_term_expirations' own comment for why this is a real,
    if rare, problem and not something that needs per-symbol tuning.

    Deliberately NOT the same GammaAggregate that Gamma Flip/Walls/Max Pain
    use (CalculateGammaExposureOrchestrator, above) -- those want the real
    exposure of the whole book, every expiration included, a different,
    equally valid question from "what does near-term gamma structure look
    like." Computed fresh from the already-stored chain snapshot
    (get_latest_chain_snapshot) on every request, not persisted -- this
    never runs on the scheduler's own write cycle, so it can't add to that
    cycle's own duration or memory (see scheduler.py's own docstring on
    why that budget already matters).
    """

    def __init__(
        self,
        storage: IStorage,
        greeks: CalculateGreeksUseCase,
        aggregate: CalculateGammaAggregateUseCase,
        window_days: int = NEAR_TERM_GAMMA_PROFILE_WINDOW_DAYS,
    ) -> None:
        self._storage = storage
        self._greeks = greeks
        self._aggregate = aggregate
        self._window_days = window_days

    def execute(self, underlying: str) -> GammaAggregate:
        chain = self._storage.get_latest_chain_snapshot(underlying)
        if chain is None:
            raise NotFoundError(f"No option chain found for {underlying.upper()}")
        near_term_chain = _filter_to_near_term_expirations(chain, self._window_days)
        enriched_chain = self._greeks.execute(near_term_chain)
        return self._aggregate.execute(enriched_chain)
