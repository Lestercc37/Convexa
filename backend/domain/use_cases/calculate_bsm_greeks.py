from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

# Black, F., & Scholes, M. (1973). "The Pricing of Options and Corporate
# Liabilities." Journal of Political Economy, 81(3), 637-654.
# Merton, R. C. (1973). "Theory of Rational Option Pricing." Bell Journal
# of Economics and Management Science, 4(1), 141-183.
# Haug, E. G. (2007). "The Complete Guide to Option Pricing Formulas"
# (2nd ed.). McGraw-Hill. — closed-form vanna/charm used below.
#
# Built for ThetaDataProvider: its Options Standard subscription exposes
# bid/ask/delta/theta/vega/IV directly (first-order greeks), but gamma/
# vanna/charm require the Professional tier, which isn't available.
# These three are calculated here instead, from data already on hand
# (spot, strike, rate, IV, time to expiration) — same no-dividend (q=0)
# closed-form formulas, verified by hand against a known textbook case
# (S=K=100, r=5%, sigma=20%, T=1yr -> gamma=0.018762, vanna=-0.281430,
# charm=-0.065667) and cross-checked against ThetaData's own reported
# delta for a real contract before trusting the implementation.
#
# No dividend yield (q=0): gamma, vanna, and charm are each identical for
# calls and puts — Vega has no call/put term at all (so neither does its
# derivative, vanna), and Delta_put = Delta_call - 1 is a T-independent
# constant (so charm, d(Delta)/dT, is the same for both). This module
# deliberately takes no `right`/call-put parameter.


@dataclass(frozen=True, slots=True)
class BsmGreeks:
    gamma: Decimal
    vanna: Decimal
    charm: Decimal


def calculate_bsm_greeks(
    spot: Decimal,
    strike: Decimal,
    rate: Decimal,
    volatility: Decimal,
    time_to_expiration: Decimal,
) -> BsmGreeks:
    """Gamma, vanna, and charm via Black-Scholes-Merton (no dividend yield).

    `time_to_expiration` is in years (ACT/365) — for same-day expirations,
    the caller is responsible for computing it as real elapsed seconds to
    market close, not calendar days (confirmed against ThetaData's own
    reported delta on real 0DTE contracts across two points in the
    trading day; whole-calendar-day counts collapse to zero for 0DTE).

    A non-positive time to expiration or volatility has no well-defined
    second derivative here (division by zero) — returns all-zero greeks
    rather than raising, matching this project's convention for a
    degenerate/expired input (same spirit as `calculate_bvc_split`'s
    sigma == 0 fallback).
    """
    if time_to_expiration <= 0 or volatility <= 0:
        return BsmGreeks(gamma=Decimal(0), vanna=Decimal(0), charm=Decimal(0))

    s = float(spot)
    k = float(strike)
    r = float(rate)
    sigma = float(volatility)
    t = float(time_to_expiration)
    sqrt_t = math.sqrt(t)

    d1 = (math.log(s / k) + (r + sigma**2 / 2) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    phi_d1 = math.exp(-(d1**2) / 2) / math.sqrt(2 * math.pi)

    gamma = phi_d1 / (s * sigma * sqrt_t)
    vanna = -phi_d1 * d2 / sigma
    charm = -phi_d1 * (2 * r * t - d2 * sigma * sqrt_t) / (2 * t * sigma * sqrt_t)

    return BsmGreeks(
        gamma=Decimal(str(gamma)),
        vanna=Decimal(str(vanna)),
        charm=Decimal(str(charm)),
    )
