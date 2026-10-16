from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backend.domain.entities import MarketPrice
from backend.domain.use_cases.calculate_anchored_vwap import (
    calculate_anchored_vwap,
    calculate_anchored_vwap_series,
    calculate_proxy_anchored_vwap,
    calculate_proxy_anchored_vwap_series,
    calculate_session_open,
    opening_reference,
)

SESSION_OPEN_UTC = datetime(2026, 8, 3, 13, 30, tzinfo=UTC)  # 9:30 ET (EDT, UTC-4)


def _reading(minutes_after_open: int, price: str, volume: int) -> MarketPrice:
    return MarketPrice(
        symbol="SPY",
        as_of=SESSION_OPEN_UTC + timedelta(minutes=minutes_after_open),
        price=Decimal(price),
        volume=volume,
    )


def _index_reading(minutes_after_open: int, price: str) -> MarketPrice:
    # Pure indices always report volume=0 (see AnchoredVwap's own docstring) --
    # the proxy calculation only ever reads .price off these, never .volume.
    return MarketPrice(
        symbol="SPX",
        as_of=SESSION_OPEN_UTC + timedelta(minutes=minutes_after_open),
        price=Decimal(price),
        volume=0,
    )


def test_calculate_session_open_returns_930_et_in_utc() -> None:
    as_of = datetime(2026, 8, 3, 19, 5, tzinfo=UTC)

    anchor = calculate_session_open(as_of)

    assert anchor.astimezone(UTC) == SESSION_OPEN_UTC


def test_anchored_vwap_weights_by_delta_volume_not_simple_average() -> None:
    readings = [
        _reading(5, "550", 800),  # first reading: interval volume = 800 (baseline 0)
        _reading(10, "560", 1000),  # interval volume = 1000 - 800 = 200
        _reading(15, "540", 2000),  # interval volume = 2000 - 1000 = 1000
    ]
    as_of = SESSION_OPEN_UTC + timedelta(minutes=15)

    result = calculate_anchored_vwap(readings, as_of)

    # (550*800 + 560*200 + 540*1000) / (800+200+1000) = 1,092,000 / 2,000 = 546
    assert result.value == Decimal(546)
    assert result.provisional is False
    assert result.sample_count == 3
    assert result.anchor_time == SESSION_OPEN_UTC
    # Simple (unweighted) average of the three prices would be 550, not 546 —
    # confirms volume weighting, not a plain average, drives the result.
    assert result.value != (Decimal(550) + Decimal(560) + Decimal(540)) / 3


def test_anchored_vwap_excludes_readings_before_session_open() -> None:
    pre_market = MarketPrice(
        symbol="SPY",
        as_of=SESSION_OPEN_UTC - timedelta(minutes=1),
        price=Decimal(500),
        volume=50_000,
    )
    first_session_reading = _reading(1, "550", 100)
    as_of = SESSION_OPEN_UTC + timedelta(minutes=1)

    result = calculate_anchored_vwap([pre_market, first_session_reading], as_of)

    assert result.sample_count == 1
    assert result.value == Decimal(550)


def test_anchored_vwap_is_provisional_with_no_readings_since_open() -> None:
    as_of = SESSION_OPEN_UTC + timedelta(minutes=1)

    result = calculate_anchored_vwap([], as_of)

    assert result.value is None
    assert result.provisional is True
    assert result.sample_count == 0
    assert result.anchor_time == SESSION_OPEN_UTC


def test_anchored_vwap_is_provisional_when_session_volume_is_zero() -> None:
    as_of = SESSION_OPEN_UTC
    reading = _reading(0, "550", 0)

    result = calculate_anchored_vwap([reading], as_of)

    assert result.value is None
    assert result.provisional is True
    assert result.sample_count == 1


def test_anchored_vwap_treats_a_first_volume_above_a_later_one_as_a_stale_carry() -> None:
    # The session counter only grows. A first reading HIGHER than a later one was a carried-forward number (yesterday's
    # total or the pre-open count), not session volume: it is not counted and the baseline restarts at zero.
    readings = [
        _reading(5, "550", 1000),  # stale carry
        _reading(10, "560", 400),  # real session volume so far: 400 at 560
        _reading(15, "540", 1400),  # +1000 at 540
    ]
    as_of = SESSION_OPEN_UTC + timedelta(minutes=15)

    result = calculate_anchored_vwap(readings, as_of)

    # (560*400 + 540*1000) / 1400 = 764,000 / 1,400 = 545.714...
    assert result.value is not None and abs(result.value - Decimal("545.7142857")) < Decimal("0.0001")
    assert result.sample_count == 3


def test_anchored_vwap_survives_the_stored_volume_alternating_between_real_and_stale() -> None:
    # 2026-10-16: the scheduler wrote the real counter, the next stream tick re-wrote the stale carry (488,685 for SPY), and so on.
    stale = 488_685
    readings = [
        _reading(0, "773", stale),
        _reading(1, "772", 314_705),
        _reading(1, "772", stale),
        _reading(3, "771", 467_230),
        _reading(3, "771", stale),
        _reading(5, "770", 523_907),
        _reading(5, "770", stale),
    ]
    as_of = SESSION_OPEN_UTC + timedelta(minutes=5)

    series = calculate_anchored_vwap_series(readings, as_of)

    expected = (
        Decimal(772) * 314_705 + Decimal(771) * (467_230 - 314_705) + Decimal(770) * (523_907 - 467_230)
    ) / Decimal(523_907)
    assert abs(series[-1][1] - expected) < Decimal("0.0001")
    # never pulled by the alternation: every point stays between the lowest and the highest traded price
    assert all(Decimal(770) <= value <= Decimal(773) for _, value in series)


def test_anchored_vwap_is_unchanged_when_the_volume_never_drops() -> None:
    readings = [_reading(1, "550", 100), _reading(2, "560", 300), _reading(3, "540", 400)]
    as_of = SESSION_OPEN_UTC + timedelta(minutes=3)

    # first reading counts as-is (the counter resets at 9:30): (550*100 + 560*200 + 540*100) / 400 = 552.5
    assert calculate_anchored_vwap(readings, as_of).value == Decimal("552.5")


def test_anchored_vwap_not_applicable_for_pure_indices_regardless_of_readings() -> None:
    # A pure index (SPX/NDX/VIX) always reports volume=0 from ThetaData's
    # own index snapshot endpoint -- structurally never computable, not
    # just "not enough data yet". not_applicable must win over whatever
    # readings are passed, and must not look like provisional=True
    # (which would read as "still accumulating" for something that will
    # never arrive).
    readings = [_reading(5, "7500", 800), _reading(10, "7510", 1200)]
    as_of = SESSION_OPEN_UTC + timedelta(minutes=10)

    result = calculate_anchored_vwap(readings, as_of, not_applicable=True)

    assert result.value is None
    assert result.provisional is False
    assert result.not_applicable is True
    assert result.sample_count == 0


def test_anchored_vwap_applicable_by_default() -> None:
    result = calculate_anchored_vwap([], SESSION_OPEN_UTC)

    assert result.not_applicable is False


def test_anchored_vwap_series_has_one_point_per_reading_once_volume_weighting_is_possible() -> None:
    readings = [
        _reading(5, "550", 800),
        _reading(10, "560", 1000),
        _reading(15, "540", 2000),
    ]
    as_of = SESSION_OPEN_UTC + timedelta(minutes=15)

    series = calculate_anchored_vwap_series(readings, as_of)

    assert [t for t, _ in series] == [r.as_of for r in readings]
    # Last point must match calculate_anchored_vwap's own answer for the
    # same inputs -- same formula, same series, just the last vs. all.
    assert series[-1][1] == calculate_anchored_vwap(readings, as_of).value


def test_anchored_vwap_series_emits_no_point_while_volume_is_still_zero() -> None:
    readings = [_reading(0, "550", 0), _reading(5, "555", 0), _reading(10, "560", 100)]
    as_of = SESSION_OPEN_UTC + timedelta(minutes=10)

    series = calculate_anchored_vwap_series(readings, as_of)

    # The first two readings contribute zero interval volume (baseline
    # then still zero) -- no point fabricated for either; only the third
    # reading, where volume finally turns positive, produces one.
    assert len(series) == 1
    assert series[0][0] == readings[2].as_of


# SPX/NDX have no volume of their own (see AnchoredVwap's own docstring), so
# these approximate their VWAP from a liquid, correlated ETF (SPY/QQQ): the
# proxy's own real VWAP expressed as a ratio to the proxy's session open,
# applied to the index's own session open -- scale-free, so it works despite
# the index trading at a completely different absolute price than its proxy.
PROXY_READINGS = [
    _reading(0, "500", 1000),  # vwap = 500 (first reading, baseline volume 0)
    _reading(5, "520", 2000),  # interval vol 1000: (500*1000+520*1000)/2000 = 510
    _reading(10, "540", 4000),  # interval vol 2000: (+540*2000)/4000 = 525
]


def test_calculate_proxy_anchored_vwap_series_scales_index_open_by_proxys_own_ratio() -> None:
    index_readings = [_index_reading(0, "8000")]
    as_of = SESSION_OPEN_UTC + timedelta(minutes=10)

    series = calculate_proxy_anchored_vwap_series(index_readings, PROXY_READINGS, as_of)

    # ratio at each proxy point: 500/500=1, 510/500=1.02, 525/500=1.05 --
    # applied to the index's own 8000 open, not the proxy's 500 price.
    assert [t for t, _ in series] == [r.as_of for r in PROXY_READINGS]
    assert [v for _, v in series] == [Decimal(8000), Decimal(8160), Decimal(8400)]


def test_calculate_proxy_anchored_vwap_series_is_empty_without_an_index_reading_this_session() -> None:
    as_of = SESSION_OPEN_UTC + timedelta(minutes=10)

    series = calculate_proxy_anchored_vwap_series([], PROXY_READINGS, as_of)

    assert series == []


def test_calculate_proxy_anchored_vwap_series_is_empty_without_a_proxy_reading_this_session() -> None:
    index_readings = [_index_reading(0, "8000")]
    as_of = SESSION_OPEN_UTC + timedelta(minutes=10)

    series = calculate_proxy_anchored_vwap_series(index_readings, [], as_of)

    assert series == []


def test_calculate_proxy_anchored_vwap_series_is_empty_when_proxy_opened_at_zero() -> None:
    index_readings = [_index_reading(0, "8000")]
    proxy_readings = [_reading(0, "0", 1000)]
    as_of = SESSION_OPEN_UTC

    series = calculate_proxy_anchored_vwap_series(index_readings, proxy_readings, as_of)

    assert series == []


def test_calculate_proxy_anchored_vwap_returns_the_last_scaled_point() -> None:
    index_readings = [_index_reading(0, "8000")]
    as_of = SESSION_OPEN_UTC + timedelta(minutes=10)

    result = calculate_proxy_anchored_vwap(index_readings, PROXY_READINGS, as_of, "SPY")

    assert result.value == Decimal(8400)
    assert result.provisional is False
    assert result.not_applicable is False
    assert result.sample_count == 3
    assert result.anchor_time == SESSION_OPEN_UTC
    assert result.proxy_symbol == "SPY"


def test_calculate_proxy_anchored_vwap_is_provisional_when_the_index_has_no_reading_yet() -> None:
    # The proxy (SPY) already has session readings, but the index (SPX)
    # itself hasn't reported one yet -- provisional, not a crash, and
    # sample_count still reflects the proxy side per the function's own
    # contract (it counts proxy_readings, not index_readings).
    as_of = SESSION_OPEN_UTC + timedelta(minutes=10)

    result = calculate_proxy_anchored_vwap([], PROXY_READINGS, as_of, "SPY")

    assert result.value is None
    assert result.provisional is True
    assert result.sample_count == 3
    assert result.proxy_symbol == "SPY"


def test_opening_reference_is_the_first_minute_median_not_a_bad_first_print() -> None:
    # 2026-10-16: SPX printed 7745.32 at 09:30:02 and 7754.51 three seconds later
    base = SESSION_OPEN_UTC + timedelta(seconds=2)
    prints = [
        MarketPrice(symbol="SPX", as_of=base, price=Decimal("7745.32"), volume=0),
        MarketPrice(symbol="SPX", as_of=base + timedelta(seconds=3), price=Decimal("7754.51"), volume=0),
        MarketPrice(symbol="SPX", as_of=base + timedelta(seconds=4), price=Decimal("7752.79"), volume=0),
        MarketPrice(symbol="SPX", as_of=base + timedelta(seconds=5), price=Decimal("7753.58"), volume=0),
        MarketPrice(symbol="SPX", as_of=base + timedelta(seconds=90), price=Decimal("7700"), volume=0),
    ]

    price, complete = opening_reference(prints)

    assert price == Decimal("7753.185")  # median of the four prints inside the first minute
    assert complete is True
    assert opening_reference(prints[:3])[1] is False  # first minute not over yet: callers must not cache it
    assert opening_reference(prints[:3], as_of=base + timedelta(seconds=61))[1] is True
    assert opening_reference([]) is None


def test_proxy_vwap_is_not_shifted_by_a_bad_first_index_print() -> None:
    base = SESSION_OPEN_UTC + timedelta(seconds=2)
    index = [
        MarketPrice(symbol="SPX", as_of=base, price=Decimal("7745"), volume=0),  # bad first print
        MarketPrice(symbol="SPX", as_of=base + timedelta(seconds=3), price=Decimal("7754"), volume=0),
        MarketPrice(symbol="SPX", as_of=base + timedelta(seconds=4), price=Decimal("7754"), volume=0),
    ]
    proxy = [_reading(0, "773", 1000), _reading(1, "773", 2000)]
    as_of = SESSION_OPEN_UTC + timedelta(minutes=1)

    series = calculate_proxy_anchored_vwap_series(index, proxy, as_of)

    assert series and series[-1][1] == Decimal("7754")  # scaled from the median open (7754), not from 7745
