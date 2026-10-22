"""The 10-second "fast gamma layer" (docs/diseno-capa-rapida-gamma-2026-10-19.txt).

The REST cycle stores one gamma snapshot per symbol every ~58 s. Open interest, IV and quotes cannot arrive faster than that, but between two cycles only the SPOT and the CLOCK move,
and gamma / vanna / charm are closed-form functions of exactly those two (plus the IV and the open interest the last cycle stored). So this layer keeps the last stored chain,
re-evaluates every contract's BSM gamma / vanna / charm at the live spot and the live time to expiration, rebuilds the Tactical view through the SAME orchestrator code path
(CalculateGammaExposureOrchestrator.build_tactical) and writes ONLY the per-strike rows (gamma_fast_items). Nothing that reads the latest aggregate (levels, derived metrics, screener,
alerts) sees it: no parent aggregate row is written.

What is NOT refreshed between cycles, on purpose: IV, open interest, vega and theta (they come from the cycle's chain). Delta is shifted to first order (delta + gamma x dSpot, clamped to
[-1, 1]) so the delta exposure follows the spot instead of freezing.

Measured 2026-10-19 12:06 ET at below-normal priority: tactical build 0.01-0.11 s per symbol, BSM re-evaluation 0.00-0.02 s, chain read 0.6 s for SPX/NDX (so the chain is cached and re-read
only every `chain_refresh_seconds`)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal

from backend.domain.entities import (
    ContractType,
    DailyBar,
    GammaAggregate,
    OptionChain,
    OptionContract,
    OptionGreeks,
)
from backend.domain.ports import IStorage
from backend.domain.use_cases.calculate_atr_range import REQUIRED_DAILY_BARS
from backend.domain.use_cases.calculate_bsm_greeks import calculate_bsm_greeks
from backend.domain.use_cases.gamma import (
    TACTICAL_ENABLED_SYMBOLS,
    CalculateGammaExposureOrchestrator,
)
from backend.domain.use_cases.market_hours import EASTERN_TIME

logger = logging.getLogger(__name__)

# The first four symbols of the rollout (SPX/NDX/SPY/QQQ); the worker takes the list from settings, this is only the default.
FAST_GAMMA_DEFAULT_SYMBOLS = ("SPX", "NDX", "SPY", "QQQ")
# A live price older than this is not a live price: the pass is skipped instead of re-pricing the chain at a stale spot.
MAX_SPOT_AGE_SECONDS = 30
# The cycle's chain is re-read from the database this often (one cycle is ~58 s; a fresher chain exists as soon as one lands).
CHAIN_REFRESH_SECONDS = 30
# Relative move of the spot below which a pass is skipped: nothing moved, the stored rows would be identical (a pass is still forced after FORCE_PASS_SECONDS
# so the clock-driven charm / time decay keeps painting).
MIN_RELATIVE_SPOT_MOVE = Decimal("0.00001")
FORCE_PASS_SECONDS = 30

TimeToExpiration = Callable[[date, datetime], Decimal]


@dataclass(slots=True)
class _CachedChain:
    chain: OptionChain
    daily_bars: list[DailyBar]
    loaded_at: float


@dataclass(slots=True)
class _LastPass:
    spot: Decimal
    at: float


class FastGammaLayer:
    def __init__(
        self,
        storage: IStorage,
        orchestrator: CalculateGammaExposureOrchestrator,
        time_to_expiration: TimeToExpiration,
        risk_free_rate: Callable[[], Decimal],
        *,
        chain_refresh_seconds: float = CHAIN_REFRESH_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(EASTERN_TIME),
        itm_iv_from_twin: bool = False,
    ) -> None:
        self._storage = storage
        self._orchestrator = orchestrator
        self._time_to_expiration = time_to_expiration
        self._risk_free_rate = risk_free_rate
        self._chain_refresh_seconds = chain_refresh_seconds
        self._clock = clock
        self._now = now
        # Same rule as the REST cycle (ThetaDataProvider, Settings.thetadata_itm_iv_from_twin): a noisy in-the-money quote is re-priced with the IV of its OTM twin, so the 10-second columns do not
        # fall back to the noisy IV the cycle already replaced.
        self._itm_iv_from_twin = itm_iv_from_twin
        self._cache: dict[str, _CachedChain] = {}
        self._last_pass: dict[str, _LastPass] = {}

    def run_pass(self, symbol: str) -> int:
        """One fast pass for `symbol`; returns how many per-strike rows were written (0 = skipped: not a tactical symbol, no chain, no fresh spot, nothing moved)."""
        symbol = symbol.upper()
        if symbol not in TACTICAL_ENABLED_SYMBOLS:
            return 0
        cached = self._cached_chain(symbol)
        if cached is None:
            return 0
        now_et = self._now()
        price = self._storage.get_latest_price(symbol)
        if price is None or price.price <= 0:
            return 0
        age = (now_et - price.as_of.astimezone(EASTERN_TIME)).total_seconds()
        if age > MAX_SPOT_AGE_SECONDS:
            logger.debug("%s: fast pass skipped, live price is %.0f s old", symbol, age)
            return 0
        spot = price.price
        last = self._last_pass.get(symbol)
        clock = self._clock()
        if (
            last is not None
            and clock - last.at < FORCE_PASS_SECONDS
            and abs(spot - last.spot) <= last.spot * MIN_RELATIVE_SPOT_MOVE
        ):
            return 0
        aggregate = self._build(symbol, cached, spot, now_et)
        if not aggregate.items:
            return 0
        self._storage.save_gamma_fast_items(symbol, now_et, aggregate.view, spot, list(aggregate.items))
        self._last_pass[symbol] = _LastPass(spot=spot, at=clock)
        return len(aggregate.items)

    def _cached_chain(self, symbol: str) -> _CachedChain | None:
        cached = self._cache.get(symbol)
        clock = self._clock()
        if cached is not None and clock - cached.loaded_at < self._chain_refresh_seconds:
            return cached
        chain = self._storage.get_latest_chain_snapshot(symbol)
        if chain is None or not chain.contracts:
            return cached
        if cached is not None and chain.as_of == cached.chain.as_of:
            cached.loaded_at = clock
            return cached
        daily_bars = self._storage.get_daily_bars(symbol, REQUIRED_DAILY_BARS)
        cached = _CachedChain(chain=chain, daily_bars=daily_bars, loaded_at=clock)
        self._cache[symbol] = cached
        return cached

    def _build(self, symbol: str, cached: _CachedChain, spot: Decimal, now_et: datetime) -> GammaAggregate:
        rate = self._risk_free_rate()
        chain = cached.chain
        old_spot = chain.spot_price
        twin_iv = twin_iv_by_contract(chain.contracts, old_spot) if self._itm_iv_from_twin else {}
        contracts = tuple(self._reprice(contract, spot, old_spot, rate, now_et, twin_iv) for contract in chain.contracts)
        moved = replace(chain, spot_price=spot, contracts=contracts)
        return self._orchestrator.build_tactical(symbol, moved, cached.daily_bars)

    def _reprice(
        self,
        contract: OptionContract,
        spot: Decimal,
        old_spot: Decimal,
        rate: Decimal,
        now_et: datetime,
        twin_iv: dict[tuple[date, Decimal, ContractType], Decimal] | None = None,
    ) -> OptionContract:
        iv = contract.iv
        if iv is None or iv <= 0:
            # Same rule as the cycle: no usable IV, no gamma (calculate_bsm_greeks returns zeros). The stored greeks already are those zeros.
            return contract
        if twin_iv and is_noisy_itm_quote(contract.contract_type, contract.strike, old_spot, contract.bid, contract.ask):
            other = ContractType.PUT if contract.contract_type == ContractType.CALL else ContractType.CALL
            iv = twin_iv.get((contract.expiration, contract.strike, other), iv)
        bsm = calculate_bsm_greeks(
            spot, contract.strike, rate, iv, self._time_to_expiration(contract.expiration, now_et)
        )
        greeks = contract.greeks
        delta = min(Decimal(1), max(Decimal(-1), greeks.delta + bsm.gamma * (spot - old_spot)))
        return replace(
            contract,
            greeks=OptionGreeks(
                delta=delta,
                gamma=bsm.gamma,
                theta=greeks.theta,
                vega=greeks.vega,
                charm=bsm.charm,
                vanna=bsm.vanna,
            ),
        )


def is_noisy_itm_quote(contract_type: ContractType, strike: Decimal, spot: Decimal, bid: Decimal, ask: Decimal) -> bool:
    """An in-the-money quote whose time value (mid - intrinsic) is smaller than half its bid/ask spread: the price says almost nothing about volatility, so the IV solved from it is noise-prone
    (measured 2026-10-19: such contracts' IV runs 2-7 vol points above the OTM side of the same strike). The same rule as ThetaDataProvider._is_noisy_itm_quote and the chart's hasReadableIv."""
    if not ask >= bid:
        return False
    intrinsic = max(spot - strike, Decimal(0)) if contract_type == ContractType.CALL else max(strike - spot, Decimal(0))
    if intrinsic <= 0:
        return False
    return (bid + ask) / 2 - intrinsic < (ask - bid) / 2


def twin_iv_by_contract(contracts: tuple[OptionContract, ...], spot: Decimal) -> dict[tuple[date, Decimal, ContractType], Decimal]:
    """The IV of every readable (positive IV, not noisy-ITM) contract keyed by (expiration, strike, type): the OTM side of a strike is the reliable one and, by put-call parity, also the IV of its ITM twin."""
    twins: dict[tuple[date, Decimal, ContractType], Decimal] = {}
    for contract in contracts:
        if contract.iv is None or contract.iv <= 0:
            continue
        if is_noisy_itm_quote(contract.contract_type, contract.strike, spot, contract.bid, contract.ask):
            continue
        twins[(contract.expiration, contract.strike, contract.contract_type)] = contract.iv
    return twins
