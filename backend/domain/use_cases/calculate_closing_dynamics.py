from __future__ import annotations

from decimal import Decimal
from statistics import median

from backend.domain.entities import ClosingDynamics, GammaAggregate, GammaAggregateItem

# Initial calibration (dashboard-spec.md section 9) — not final, to be
# recalibrated with real data later, same spirit as the initial Whale
# Alerts thresholds. time_to_close_pct <= 15 is the last ~15% of the
# session (~58 minutes of a 9:30am-4:00pm ET, 6.5-hour session).
CLOSING_WINDOW_THRESHOLD_PCT = Decimal(15)

TOP_STRIKES_FOR_OI_CONCENTRATION = 3

_OI_CONCENTRATION_WEIGHT = Decimal("0.30")
_OI_CONCENTRATION_SATURATION = Decimal("0.50")
_PROXIMITY_WEIGHT = Decimal("0.25")
_PROXIMITY_SATURATION_PCT = Decimal("0.005")
_TIME_WEIGHT = Decimal("0.25")
_GAMMA_WEIGHT = Decimal("0.20")
_GAMMA_SATURATION_MULTIPLE = Decimal(10)


def _magnet_strike(items: tuple[GammaAggregateItem, ...]) -> GammaAggregateItem | None:
    if not items:
        return None
    return max(items, key=lambda item: abs(item.net_gamma))


def _oi_concentration_score(items: tuple[GammaAggregateItem, ...]) -> Decimal:
    total_oi = sum(item.open_interest for item in items)
    if total_oi <= 0:
        return Decimal(0)
    top_oi = sum(
        sorted((item.open_interest for item in items), reverse=True)[
            :TOP_STRIKES_FOR_OI_CONCENTRATION
        ]
    )
    concentration = Decimal(top_oi) / Decimal(total_oi)
    return min(concentration, _OI_CONCENTRATION_SATURATION) / _OI_CONCENTRATION_SATURATION


def _proximity_score(magnet: GammaAggregateItem, price: Decimal) -> Decimal:
    if price <= 0:
        return Decimal(0)
    distance_pct = abs(magnet.strike - price) / price
    if distance_pct <= _PROXIMITY_SATURATION_PCT:
        return Decimal(1)
    return _PROXIMITY_SATURATION_PCT / distance_pct


def _time_score(time_to_close_pct: Decimal) -> Decimal:
    return (Decimal(100) - time_to_close_pct) / Decimal(100)


def _gamma_score(magnet: GammaAggregateItem, items: tuple[GammaAggregateItem, ...]) -> Decimal:
    median_magnitude = median(abs(item.net_gamma) for item in items)
    if median_magnitude <= 0:
        return Decimal(0)
    ratio = abs(magnet.net_gamma) / (median_magnitude * _GAMMA_SATURATION_MULTIPLE)
    return min(ratio, Decimal(1))


def calculate_pin_risk_score(
    items: tuple[GammaAggregateItem, ...],
    price: Decimal,
    time_to_close_pct: Decimal,
) -> tuple[Decimal, Decimal | None]:
    """Composite 0-100 Pin Risk Score (dashboard-spec.md section 9).

    Weighted: OI concentration in the top 3 strikes (30%, saturating at
    50%+ of total OI), proximity of the magnet strike to spot (25%,
    saturating within 0.5%), remaining session time (25%, reusing
    `time_to_close_pct`), and the magnet strike's `net_gamma` against 10x
    the median `|net_gamma|` across strikes (20%, saturating at 10x).

    Without a strike breakdown (`items` empty — e.g. a `GammaAggregate`
    persisted before the items migration), only the time component can be
    computed; the strike-dependent 75% defaults to 0 and there is no
    magnet strike.
    """
    time_component = _time_score(time_to_close_pct) * _TIME_WEIGHT
    magnet = _magnet_strike(items)
    if magnet is None:
        return (time_component * 100, None)
    oi_component = _oi_concentration_score(items) * _OI_CONCENTRATION_WEIGHT
    proximity_component = _proximity_score(magnet, price) * _PROXIMITY_WEIGHT
    gamma_component = _gamma_score(magnet, items) * _GAMMA_WEIGHT
    score = (oi_component + proximity_component + time_component + gamma_component) * 100
    return (score, magnet.strike)


def calculate_charm_regime(charm_exposure: Decimal) -> str | None:
    if charm_exposure > 0:
        return "time_decay_dealers_buy"
    if charm_exposure < 0:
        return "time_decay_dealers_sell"
    return None


def calculate_vanna_interpretation(vanna_exposure: Decimal) -> str | None:
    if vanna_exposure > 0:
        return "iv_increase_dealers_buy"
    if vanna_exposure < 0:
        return "iv_increase_dealers_sell"
    return None


def calculate_closing_dynamics(
    gamma: GammaAggregate, price: Decimal, time_to_close_pct: Decimal
) -> ClosingDynamics:
    pin_score, magnet_strike = calculate_pin_risk_score(gamma.items, price, time_to_close_pct)
    return ClosingDynamics(
        time_to_close_pct=time_to_close_pct,
        active=time_to_close_pct <= CLOSING_WINDOW_THRESHOLD_PCT,
        pin_score=pin_score,
        magnet_strike=magnet_strike,
        charm_regime=calculate_charm_regime(gamma.charm_exposure),
        vanna_interpretation=calculate_vanna_interpretation(gamma.vanna_exposure),
        max_pain=gamma.max_pain,
    )
