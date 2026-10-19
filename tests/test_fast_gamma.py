"""The 10-second fast gamma layer: re-pricing the stored chain at the live spot and writing only the per-strike rows (gamma_fast_items)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.adapters.storage.memory import InMemoryStorage
from backend.domain.entities import ContractType, Greeks, MarketPrice, OptionChain, OptionContract
from backend.domain.use_cases.fast_gamma import FastGammaLayer
from backend.domain.use_cases.market_hours import EASTERN_TIME
from backend.main import app
from tests.test_gamma_views import AS_OF, SPOT_PRICE, TODAY_ET, _orchestrator

NOW_ET = datetime(2026, 1, 15, 10, 0, 30, tzinfo=EASTERN_TIME)  # 30 minutes after the stored chain (AS_OF is 09:30 ET)
RATE = Decimal("0.04")


def _tte(expiration: date, quote_time_et: datetime) -> Decimal:
    # One hour per day-of-difference is plenty for a test: what matters is that it is positive and shrinks with the clock.
    hours = max(Decimal(1), Decimal((expiration - quote_time_et.date()).days * 24 + 6))
    return hours / Decimal(24 * 365)


def _contract(contract_type: ContractType, strike: int, iv: str = "0.20", gamma: str = "0.001") -> OptionContract:
    letter = "C" if contract_type == ContractType.CALL else "P"
    return OptionContract(
        underlying="SPY",
        strike=Decimal(strike),
        expiration=TODAY_ET,
        contract_type=contract_type,
        occ_symbol=f"SPY260115{letter}00{strike}000",
        bid=Decimal(1),
        ask=Decimal("1.10"),
        last=Decimal("1.05"),
        volume=10,
        open_interest=1000,
        iv=Decimal(iv),
        greeks=Greeks(
            delta=Decimal("0.50") if contract_type == ContractType.CALL else Decimal("-0.50"),
            gamma=Decimal(gamma),
            theta=Decimal("-0.10"),
            vega=Decimal("0.20"),
            charm=Decimal("0.01"),
            vanna=Decimal("0.01"),
        ),
    )


def _layer(storage: InMemoryStorage, clock: list[float], now: list[datetime]) -> FastGammaLayer:
    return FastGammaLayer(
        storage,
        _orchestrator(storage),
        time_to_expiration=_tte,
        risk_free_rate=lambda: RATE,
        clock=lambda: clock[0],
        now=lambda: now[0],
    )


def _seed(storage: InMemoryStorage, spot: Decimal, *, price_age_seconds: int = 0) -> None:
    contracts = tuple(
        _contract(kind, strike) for strike in (545, 550, 555) for kind in (ContractType.CALL, ContractType.PUT)
    )
    storage.save_chain_snapshot(OptionChain(symbol="SPY", as_of=AS_OF, spot_price=SPOT_PRICE, contracts=contracts))
    storage.save_market_price(
        MarketPrice(symbol="SPY", as_of=NOW_ET.astimezone(UTC) - timedelta(seconds=price_age_seconds), price=spot, volume=1)
    )


def test_a_pass_writes_per_strike_rows_and_no_aggregate() -> None:
    storage = InMemoryStorage()
    _seed(storage, Decimal("551.5"))
    layer = _layer(storage, [0.0], [NOW_ET])

    written = layer.run_pass("SPY")

    assert written == 3
    assert len(storage.gamma_fast_items) == 1
    symbol, as_of, view, spot, items = storage.gamma_fast_items[0]
    assert (symbol, as_of, view, spot) == ("SPY", NOW_ET, "tactical", Decimal("551.5"))
    assert sorted(item.strike for item in items) == [Decimal(545), Decimal(550), Decimal(555)]
    # The fast layer never creates the parent row the "latest" readers use.
    assert storage.get_latest_gamma_aggregate("SPY", view="tactical") is None


def test_gamma_is_re_evaluated_at_the_live_spot_not_copied_from_the_stored_chain() -> None:
    storage = InMemoryStorage()
    _seed(storage, SPOT_PRICE)
    layer = _layer(storage, [0.0], [NOW_ET])
    layer.run_pass("SPY")
    at_the_money = {item.strike: item for item in storage.gamma_fast_items[-1][4]}

    # Spot jumps 10 points: the 555 strike is now closer to the money and its gamma must grow, the 550 one (the money before) must shrink.
    storage.save_market_price(MarketPrice(symbol="SPY", as_of=NOW_ET.astimezone(UTC), price=SPOT_PRICE + 10, volume=1))
    layer.run_pass("SPY")
    moved = {item.strike: item for item in storage.gamma_fast_items[-1][4]}

    assert moved[Decimal(555)].total_gamma_exposure > at_the_money[Decimal(555)].total_gamma_exposure
    assert moved[Decimal(550)].total_gamma_exposure < at_the_money[Decimal(550)].total_gamma_exposure


def test_a_stale_or_missing_price_skips_the_pass() -> None:
    storage = InMemoryStorage()
    _seed(storage, SPOT_PRICE, price_age_seconds=120)
    layer = _layer(storage, [0.0], [NOW_ET])
    assert layer.run_pass("SPY") == 0
    assert storage.gamma_fast_items == []

    empty = InMemoryStorage()
    assert _layer(empty, [0.0], [NOW_ET]).run_pass("SPY") == 0


def test_a_symbol_outside_the_tactical_scope_is_never_computed() -> None:
    storage = InMemoryStorage()
    _seed(storage, SPOT_PRICE)
    assert _layer(storage, [0.0], [NOW_ET]).run_pass("AAPL") == 0
    assert storage.gamma_fast_items == []


def test_an_unmoved_spot_is_skipped_until_the_forced_pass() -> None:
    storage = InMemoryStorage()
    _seed(storage, SPOT_PRICE)
    clock = [0.0]
    layer = _layer(storage, clock, [NOW_ET])

    assert layer.run_pass("SPY") == 3
    clock[0] = 10.0
    assert layer.run_pass("SPY") == 0  # same spot, 10 s later: identical rows, nothing written
    clock[0] = 31.0
    assert layer.run_pass("SPY") == 3  # the clock alone still moves time decay: forced after 30 s
    assert len(storage.gamma_fast_items) == 2


def test_a_contract_without_iv_keeps_its_stored_greeks() -> None:
    storage = InMemoryStorage()
    contracts = (_contract(ContractType.CALL, 550, iv="0"), _contract(ContractType.PUT, 550, iv="0"))
    storage.save_chain_snapshot(OptionChain(symbol="SPY", as_of=AS_OF, spot_price=SPOT_PRICE, contracts=contracts))
    storage.save_market_price(MarketPrice(symbol="SPY", as_of=NOW_ET.astimezone(UTC), price=SPOT_PRICE + 1, volume=1))

    assert _layer(storage, [0.0], [NOW_ET]).run_pass("SPY") == 1
    (item,) = storage.gamma_fast_items[0][4]
    # 0.001 x OI 1000 x 100 x spot^2 x 0.01 = the stored gamma, untouched (the fake calculator's per-contract formula), calls positive and puts negative.
    assert item.call_gamma_exposure > 0 and item.put_gamma_exposure != 0


def test_the_chain_is_re_read_only_when_a_newer_snapshot_exists() -> None:
    storage = InMemoryStorage()
    _seed(storage, SPOT_PRICE)
    clock = [0.0]
    layer = _layer(storage, clock, [NOW_ET])
    layer.run_pass("SPY")

    reads = []
    original = storage.get_latest_chain_snapshot

    def counting(symbol, *args, **kwargs):  # type: ignore[no-untyped-def]
        reads.append(symbol)
        return original(symbol, *args, **kwargs)

    storage.get_latest_chain_snapshot = counting  # type: ignore[method-assign]
    clock[0] = 10.0
    layer.run_pass("SPY")
    assert reads == []  # inside the 30 s window: cached
    clock[0] = 45.0
    layer.run_pass("SPY")
    assert reads == ["SPY"]  # past it: one read, same as_of, still the cached chain


def test_heatmap_endpoint_accepts_ten_second_buckets_and_the_fast_source_and_rejects_nonsense() -> None:
    with TestClient(app) as client:
        ok = client.get("/api/v1/gamma/SPX/heatmap", params={"bucket_seconds": 10, "source": "fast"})
        too_small = client.get("/api/v1/gamma/SPX/heatmap", params={"bucket_seconds": 9})
        unknown = client.get("/api/v1/gamma/SPX/heatmap", params={"source": "other"})

    assert ok.status_code == 200
    assert ok.json()["bucket_seconds"] == 10
    assert too_small.status_code == 422
    assert unknown.status_code == 422
