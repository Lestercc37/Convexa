from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal

from backend.domain.entities import DailyBar, GammaAggregate, GammaView, OptionChain
from backend.domain.ports import IAsyncMarketReadStorage, IStorage
from backend.domain.use_cases.calculate_atr_range import REQUIRED_DAILY_BARS
from backend.domain.use_cases.calculate_gamma_aggregate import (
    CalculateGammaAggregateUseCase,
)
from backend.domain.use_cases.calculate_gamma_flip import CalculateGammaFlipUseCase
from backend.domain.use_cases.calculate_greeks import CalculateGreeksUseCase
from backend.domain.use_cases.calculate_max_pain import CalculateMaxPainUseCase
from backend.domain.use_cases.calculate_near_the_money_width import (
    calculate_near_the_money_width,
)
from backend.domain.use_cases.calculate_walls import CalculateWallsUseCase
from backend.domain.use_cases.errors import NotFoundError
from backend.domain.use_cases.market_hours import EASTERN_TIME


def get_gamma_exposure(
    storage: IStorage, underlying: str, view: GammaView = "structural"
) -> GammaAggregate:
    gamma = storage.get_latest_gamma_aggregate(underlying, view=view)
    if gamma is None:
        raise NotFoundError(f"No gamma aggregate found for {underlying.upper()}")
    return gamma


async def get_gamma_exposure_async(
    storage: IAsyncMarketReadStorage, underlying: str, view: GammaView = "structural"
) -> GammaAggregate:
    gamma = await storage.get_latest_gamma_aggregate(underlying, view=view)
    if gamma is None:
        raise NotFoundError(f"No gamma aggregate found for {underlying.upper()}")
    return gamma


def get_gamma_history(
    storage: IStorage,
    underlying: str,
    start: datetime,
    end: datetime,
    view: GammaView = "structural",
) -> list[GammaAggregate]:
    return storage.get_gamma_history(underlying, start, end, view=view)


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
        """Structural view only -- see this class's own execute_both()
        for why most real callers want that instead. Kept as its own
        focused method (not execute_both()[0]) so it stays independently
        callable/testable without paying for a tactical build it doesn't
        need, and so every existing caller/test of the pre-2026-09-25
        public contract keeps working unchanged."""
        chain = self._fetch_chain(underlying)
        daily_bars = self._storage.get_daily_bars(underlying, REQUIRED_DAILY_BARS)
        result = self._build_view(
            underlying, chain, daily_bars, _structural_window_days(underlying), None, "structural"
        )
        self._storage.save_gamma_aggregate(result)
        return result

    def execute_tactical(self, underlying: str) -> GammaAggregate:
        """Tactical view only -- see execute()'s own docstring for why
        this stays a separate, focused method from execute_both()."""
        chain = self._fetch_chain(underlying)
        daily_bars = self._storage.get_daily_bars(underlying, REQUIRED_DAILY_BARS)
        anchor = chain.as_of.astimezone(EASTERN_TIME).date()
        result = self._build_view(
            underlying, chain, daily_bars, TACTICAL_WINDOW_DAYS, anchor, "tactical"
        )
        self._storage.save_gamma_aggregate(result)
        return result

    def execute_both(self, underlying: str) -> tuple[GammaAggregate, GammaAggregate]:
        """The real entry point for the periodic refresh cycle (see
        RefreshUnderlyingSnapshotUseCase) -- builds and persists the
        tactical GammaAggregate for `underlying` on every call, and the
        structural one only when STRUCTURAL_REFRESH_INTERVAL has actually
        elapsed since the last one persisted (see that constant's own
        comment for why) -- both from a single chain/daily-bars fetch.
        Fetching once (rather than calling execute() then
        execute_tactical(), which would fetch the same storage rows a
        second time for no reason) is the whole justification for this
        method existing separately from the two single-view ones above --
        the expiration-window filter is a pure in-memory operation on an
        already-fetched chain, so there's nothing view-specific about the
        fetch itself.

        A skipped cycle returns the still-current, previously-persisted
        structural aggregate unchanged -- not a stale flag, not a
        re-fetch, just the same row every consumer already reads via
        get_latest_gamma_aggregate() the rest of the time between
        refreshes.
        """
        chain = self._fetch_chain(underlying)
        daily_bars = self._storage.get_daily_bars(underlying, REQUIRED_DAILY_BARS)

        anchor = chain.as_of.astimezone(EASTERN_TIME).date()
        tactical = self._build_view(
            underlying, chain, daily_bars, TACTICAL_WINDOW_DAYS, anchor, "tactical"
        )
        self._storage.save_gamma_aggregate(tactical)

        existing_structural = self._storage.get_latest_gamma_aggregate(underlying, view="structural")
        structural_is_fresh = (
            existing_structural is not None
            and chain.as_of - existing_structural.as_of < STRUCTURAL_REFRESH_INTERVAL
        )
        if structural_is_fresh:
            assert existing_structural is not None  # narrows for the type checker
            structural = existing_structural
        else:
            structural = self._build_view(
                underlying, chain, daily_bars, _structural_window_days(underlying), None, "structural"
            )
            self._storage.save_gamma_aggregate(structural)

        return structural, tactical

    def _fetch_chain(self, underlying: str) -> OptionChain:
        chain = self._storage.get_latest_chain_snapshot(underlying)
        if chain is None:
            raise NotFoundError(f"No option chain found for {underlying.upper()}")
        return chain

    def _build_view(
        self,
        underlying: str,
        chain: OptionChain,
        daily_bars: list[DailyBar],
        window_days: int,
        anchor: date | None,
        view: GammaView,
    ) -> GammaAggregate:
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
        near_term_chain = _filter_to_near_term_expirations(chain, window_days, anchor=anchor)
        if not near_term_chain.contracts:
            # Honest empty result, tactical-only in practice (the
            # structural window is wide enough that every active symbol
            # has always had something in range) -- e.g. an individual
            # stock that only lists Friday weeklies, on a day that isn't
            # within 2 real calendar days of one. Deliberately NOT the
            # same "fall back to the full chain" trick the narrow-width
            # guard below uses for a degenerate ATR width -- that guard
            # exists because an empty STRIKE range is almost certainly a
            # data anomaly, but an empty TACTICAL EXPIRATION range is a
            # normal, expected, honest outcome. Falling back would
            # silently show structural numbers under the tactical label.
            return GammaAggregate(symbol=chain.symbol, as_of=chain.as_of, view=view)
        enriched_chain = self._greeks.execute(near_term_chain)

        # Gamma Flip needs to search wherever dealer net gamma actually
        # crosses zero, which can legitimately sit further from spot than
        # Call Wall/Put Wall/Net GEX ever do -- confirmed live 2026-09-21,
        # SPX's real near-term crossing sat ~$175 from spot, outside the
        # regular (1.5x ATR) width. Call Wall/Put Wall/Net GEX/the
        # aggregate items behind the GEX-by-strike histogram all keep
        # using the narrower width below (unchanged, already verified
        # against a real reference platform) -- only Gamma Flip's own
        # search uses the wider one.
        wide_aggregate = self._aggregate.execute(enriched_chain)
        gamma_flip = self._gamma_flip.execute(wide_aggregate, enriched_chain.spot_price)

        narrow_width = calculate_near_the_money_width(
            underlying, daily_bars, enriched_chain.spot_price
        )
        narrow_contracts = tuple(
            contract
            for contract in enriched_chain.contracts
            if abs(contract.strike - enriched_chain.spot_price) <= narrow_width
        )
        # Degenerate width guard, same reasoning as the provider's own
        # _filter_near_the_money fallback: an unusually calm ATR window
        # narrower than this symbol's own strike spacing must not zero
        # out Call Wall/Put Wall/Net GEX entirely.
        narrow_chain = replace(
            enriched_chain, contracts=narrow_contracts or enriched_chain.contracts
        )

        aggregate = self._aggregate.execute(narrow_chain)

        if view == "structural":
            # Call Wall/Put Wall exclude 0DTE (expiring "today" in ET, the
            # market's own trading day, not UTC), same documented precedent
            # as the structural window above: ExpireWorthless explicitly
            # excludes 0DTE from its own wall calculation so one expiring
            # strike can't distort the levels. Confirmed live, 2026-09-23,
            # SPX: a single 0DTE contract at a near-the-money strike (539
            # calls / 2,793 puts open interest -- thin, nowhere near this
            # chain's largest OI strikes) carried enough dealer gamma to
            # hijack both walls onto its own strike and away from a stable
            # reference level, because BSM gamma diverges as an ATM
            # option's time-to-expiry approaches zero -- a real property of
            # the formula, not a data error, but not what a "wall" (an
            # inventory concentration dealers actually have to hedge across
            # more than a few remaining hours) is meant to represent either.
            # Net GEX/Gamma Flip/the GEX-by-strike histogram (`aggregate`
            # itself, above) keep including 0DTE unchanged -- only wall
            # *selection* excludes it, and only for the structural view:
            # tactical's entire purpose is measuring today's own expiring
            # flow, so excluding 0DTE from ITS walls would exclude the
            # exact contracts it exists to read.
            today_et = enriched_chain.as_of.astimezone(EASTERN_TIME).date()
            walls_contracts = tuple(
                contract for contract in narrow_chain.contracts if contract.expiration != today_et
            )
            # Degenerate guard, same reasoning as the narrow-width fallback
            # above: if every near-the-money contract happens to expire
            # today (a real possibility on 0DTE-heavy underlyings like
            # SPX), excluding it entirely would leave nothing to select a
            # wall from.
            walls_chain = replace(
                narrow_chain, contracts=walls_contracts or narrow_chain.contracts
            )
        else:
            walls_chain = narrow_chain
        walls_aggregate = self._aggregate.execute(walls_chain)
        walls = self._walls.execute(walls_aggregate)
        max_pain = self._max_pain.execute(narrow_chain)
        contract_multiplier = Decimal(100)
        # Vega/Theta/Charm/Delta exposure below all carry a `*
        # enriched_chain.spot_price` multiplier, same as Vanna's own
        # `dealer_gamma_exposure`-sibling formula just below -- confirmed
        # missing here, 2026-09-23, comparing against GEXBot's own
        # documented DEX formula (100 x delta x OI x share_price): without
        # it, these four were the only exposures in this codebase reporting
        # a per-contract-unit sensitivity instead of a dollar notional, an
        # inconsistency with no compensating comment or intent behind it,
        # and not compensated for anywhere on the frontend either
        # (dashboard.tsx's EXPOSURE_FORMAT is a plain compact number, no
        # currency styling or client-side price multiplication).
        vega_exposure = sum(
            (
                contract.greeks.vega
                * Decimal(contract.open_interest)
                * contract_multiplier
                * enriched_chain.spot_price
                for contract in narrow_chain.contracts
            ),
            Decimal(0),
        )
        theta_exposure = sum(
            (
                contract.greeks.theta
                * Decimal(contract.open_interest)
                * contract_multiplier
                * enriched_chain.spot_price
                for contract in narrow_chain.contracts
            ),
            Decimal(0),
        )
        charm_exposure = sum(
            (
                contract.greeks.charm
                * Decimal(contract.open_interest)
                * contract_multiplier
                * enriched_chain.spot_price
                for contract in narrow_chain.contracts
            ),
            Decimal(0),
        )
        vanna_exposure = sum(
            (
                contract.greeks.vanna
                * Decimal(contract.open_interest)
                * contract_multiplier
                * enriched_chain.spot_price
                for contract in narrow_chain.contracts
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
                contract.greeks.delta
                * Decimal(contract.open_interest)
                * contract_multiplier
                * enriched_chain.spot_price
                for contract in narrow_chain.contracts
            ),
            Decimal(0),
        )

        return replace(
            aggregate,
            view=view,
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
            # Same reasoning as gamma_flip just above, now that call_wall/
            # put_wall are properly nullable too -- None means "no valid
            # candidate found on that side" (e.g. every in-range strike
            # nets the same sign), a real, distinct outcome from "the wall
            # is at strike 0". `aggregate.call_wall`/`.put_wall` are no
            # longer consulted as a fallback -- that was always just the
            # dataclass's own unset default (never a real computed value;
            # CalculateGammaAggregateUseCase never sets these itself), so
            # it silently manufactured a fake $0 wall instead of an honest
            # "not found". Confirmed live, 2026-09-25: SPX's own Tactical
            # window hit this for real, and the fake $0 dragged the price
            # chart's autoscale down to include it, visually collapsing
            # every other level into a sliver at the top of the chart.
            call_wall=walls.call_wall.strike if walls.call_wall is not None else None,
            put_wall=walls.put_wall.strike if walls.put_wall is not None else None,
            max_pain=max_pain.max_pain_strike,
            # Net GEX / dealer_position (the "gamma regime" the dashboard
            # badge shows) now sourced from wide_aggregate -- the same
            # complete, ATR-unrestricted window Gamma Flip's own crossing
            # search already uses -- instead of the ATR-narrow window
            # below (walls_aggregate/aggregate), which can silently
            # exclude real gamma exposure sitting just outside that
            # radius. Confirmed with the user, 2026-09-24, from a real
            # trading scenario: price crossing a LOCAL zero-crossing near
            # a support level does not necessarily mean the regime that
            # actually governs dealer hedging flow has changed, if the
            # broader book (outside the narrow radius) is still dominated
            # by the opposite sign -- a regime reading tied to that
            # narrower slice would have been actively misleading in that
            # scenario, not just cosmetically inconsistent with the flip
            # line. Call Wall/Put Wall/Max Pain/the Greek exposures below
            # are UNCHANGED (still the ATR-narrow window, already tuned
            # and verified against a real reference platform) -- this
            # only widens the regime/Net GEX reading itself.
            total_market_gamma=wide_aggregate.total_market_gamma,
            positive_gamma=wide_aggregate.positive_gamma,
            negative_gamma=wide_aggregate.negative_gamma,
            total_gamma=wide_aggregate.total_gamma,
            net_gamma=wide_aggregate.net_gamma,
            dealer_gamma_notional=wide_aggregate.dealer_gamma_notional,
            vega_exposure=vega_exposure,
            theta_exposure=theta_exposure,
            charm_exposure=charm_exposure,
            vanna_exposure=vanna_exposure,
            delta_exposure=delta_exposure,
        )


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
# like SPX's LEAPS. Still comfortably excludes that class of outlier at
# any value well under a year.
#
# Widened 30 -> 90, 2026-09-24, per the user's own direction: 90 days
# (including 0DTE) is the window convention referenced across GEX
# literature/reference platforms, and the user wants to observe this
# value against live market behavior and iterate from there rather than
# treat 30 as final. Not a correction of the original 30-day choice --
# that was itself deliberate and verified (see this comment's own
# history) -- this is a considered adjustment, to be revisited based on
# what live data actually shows.
#
# Replaced with a per-symbol tiered window, 2026-09-25: a methodology
# document from a domain expert (shared and reviewed with the user the
# same day) specifically recommends 0-30 DTE for liquid indices/ETFs and
# 0-45 DTE for individual stocks -- narrower than the flat 90 days above
# for every symbol, and tiered by liquidity/listing cadence rather than
# uniform. This also connects to a real, measured cost of the flat-90
# window found the same day: /market/SPX taking 7-8s (vs 2-3s for other
# symbols) because SPX's own near-term chain snapshot had grown
# proportionally larger. UnderlyingKind (EQUITY/INDEX/FUTURE) does NOT
# distinguish a liquid ETF (SPY/QQQ/IWM/DIA, all EQUITY) from an
# individual stock (AAPL/TSLA, also EQUITY) -- this table classifies
# explicitly per symbol instead, same pattern as FIXED_WIDTH_BY_SYMBOL in
# calculate_near_the_money_width.py (a different, orthogonal axis --
# strike-price width, not expiration-date window -- but the same
# "most symbols follow a rule, a few need an explicit table" shape).
STRUCTURAL_WINDOW_DAYS_BY_SYMBOL: dict[str, int] = {
    # Liquid indices/ETFs -- 30 days.
    "SPX": 30,
    "NDX": 30,
    "VIX": 30,
    "SPY": 30,
    "QQQ": 30,
    "IWM": 30,
    "DIA": 30,
    # ES has no single-name listing cadence of its own -- it tracks the
    # S&P 500 in index points (see calculate_near_the_money_width.py's
    # own comment on ES's fixed strike width), so it takes the index
    # tier, not the individual-stock one.
    "ES": 30,
    # Individual stocks -- 45 days.
    "AAPL": 45,
    "MSFT": 45,
    "NVDA": 45,
    "TSLA": 45,
    "META": 45,
    "AMZN": 45,
    "GOOGL": 45,
}
# Defensive fallback for a symbol added to ACTIVE_UNDERLYINGS later
# without an entry above -- defaults to the wider, more conservative
# individual-stock tier rather than silently narrowing an unclassified
# symbol's window.
_DEFAULT_STRUCTURAL_WINDOW_DAYS = 45


def _structural_window_days(symbol: str) -> int:
    return STRUCTURAL_WINDOW_DAYS_BY_SYMBOL.get(symbol.upper(), _DEFAULT_STRUCTURAL_WINDOW_DAYS)


# Tactical: today's own expiring flow ("fuerza intradia" per the same
# methodology document) -- fixed at 0-2 DTE for every symbol, no tiering
# (unlike the structural window above). Anchored to `chain.as_of`
# converted to Eastern time (see execute_tactical/execute_both), not the
# nearest LISTED expiration the way the structural window is -- most
# individual stocks only list Friday weeklies, so on most days their
# nearest listed expiration is NOT within 2 real calendar days of today.
# Anchoring to the nearest listing instead of real "today" would silently
# turn "0-2 DTE" into "0-2 days from whatever's listed, however far that
# really is" for those symbols -- confirmed with the user this is the
# WRONG tradeoff: an empty tactical result on days a symbol lists nothing
# within 0-2 real days is the honest answer, not a bug to route around.
TACTICAL_WINDOW_DAYS = 2

# Structural refresh throttle, added 2026-09-25 per the user's own
# trading style: a scalper/day trader reads Tactical as their working
# set (needs to be as fresh as every scheduler cycle can make it) and
# Structural as background macro context (Call Wall/Put Wall/Gamma Flip
# on the wider window) that doesn't need the same resolution. Recomputing
# and persisting a full Structural build every cycle -- BSM greeks,
# three aggregations, Gamma Flip's own wide search, walls, max pain, all
# five Greek-exposure sums, plus two Postgres writes (gamma_aggregates +
# its per-strike items) -- was pure cost with no corresponding benefit
# once nothing is actually looking at it that often. Tactical is
# unaffected -- see execute_both's own skip logic below, gated on this
# threshold rather than removing Structural's own freshness entirely.
STRUCTURAL_REFRESH_INTERVAL = timedelta(minutes=15)


def _filter_to_near_term_expirations(
    chain: OptionChain, window_days: int, anchor: date | None = None
) -> OptionChain:
    """Filters `chain` to contracts within `window_days` of `anchor`.

    `anchor=None` (every structural caller): the window's own lower
    bound is implicitly satisfied by using the nearest LISTED expiration
    as the anchor itself -- unchanged behavior from before this function
    gained an explicit anchor parameter.

    `anchor=<a real date>` (tactical only): the window is anchored to
    that date regardless of what's actually listed -- see
    TACTICAL_WINDOW_DAYS' own comment for why this distinction matters.
    The explicit lower bound (`anchor <= expiration`) only does real work
    in this branch; for the anchor-is-the-minimum case it's always true.
    """
    if not chain.contracts:
        return chain
    if anchor is None:
        anchor = min(contract.expiration for contract in chain.contracts)
    cutoff = anchor + timedelta(days=window_days)
    near_term_contracts = tuple(
        contract
        for contract in chain.contracts
        if anchor <= contract.expiration <= cutoff
    )
    return replace(chain, contracts=near_term_contracts)
