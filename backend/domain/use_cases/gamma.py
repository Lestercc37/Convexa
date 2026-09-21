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

        # Near-term only, not the full chain -- confirmed live, 2026-09-21,
        # against a real reference platform's own numbers (SPY/SPX): summing
        # gamma exposure across every expiration a symbol lists (P1's own
        # wildcard fetch, dozens for SPX) inflates Call Wall/Put Wall/Gamma
        # Flip/Max Pain/Net GEX to a multiple of what every professional GEX
        # provider reports, none of which blend that way (SpotGamma, FlashAlpha,
        # ExpireWorthless all confirmed to key off a handful of near-term
        # expirations, not the whole chain -- see
        # _filter_to_near_term_expirations' own comment for the exact SPX
        # LEAPS incident this was first caught on). This is what every
        # consumer of this orchestrator's result already implicitly expected
        # -- Gamma Flip/Walls/Max Pain were never designed to average across
        # a LEAPS contract 5 years out diluting today's real dealer exposure.
        near_term_chain = _filter_to_near_term_expirations(
            chain, NEAR_TERM_GAMMA_PROFILE_WINDOW_DAYS
        )
        enriched_chain = self._greeks.execute(near_term_chain)
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


# Root-cause fix (2026-09-21): every headline number this orchestrator
# produces -- Call Wall, Put Wall, Gamma Flip, Max Pain, Net GEX -- used to
# be computed from the FULL chain, every expiration a symbol lists summed
# together (P1's own wildcard fetch: 20-58+ expirations depending on
# symbol). Confirmed live against a real reference platform's own numbers,
# matched strike-by-strike and contract-by-contract (SPY/SPX, same moment,
# same open interest down to the exact contract): summing across every
# expiration inflates these numbers to several times what every
# professional GEX provider reports (SpotGamma, FlashAlpha, ExpireWorthless
# -- none blend the full chain into one number; ExpireWorthless explicitly
# excludes 0DTE from its own wall calculation specifically so one expiring
# strike can't distort the levels). The walls/flip/max-pain strikes
# themselves were already landing close to the reference's own top strikes
# even before this fix -- it's specifically the blended-across-every-
# expiration dollar magnitude that was wrong, not the strike selection.
#
# First caught via the GEX-by-strike chart (P-E): a single 2031 LEAPS
# listing on SPX (1,915 DTE), contributing just 2 strikes (7200, 8000) with
# real, if comparatively small, open interest, doubled that chart's visible
# x-axis range on its own. Confirmed NOT a per-symbol tuning problem --
# AAPL and NDX both stay tight across their own full expiration range (851
# and 1,187 DTE respectively); it's specifically rare far-outlier listings
# like SPX's LEAPS. 30 days comfortably covers every real near-term
# expiration while excluding that class of outlier.
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
