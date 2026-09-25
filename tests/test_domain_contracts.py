from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from backend.adapters.providers.mock.fake import FakeGreeksCalculator
from backend.adapters.providers.mock.gamma_aggregate import FakeGammaAggregateCalculator
from backend.adapters.providers.mock.gamma_exposure import FakeGammaExposureCalculator
from backend.adapters.providers.mock.gamma_flip import FakeGammaFlipCalculator
from backend.adapters.providers.mock.max_pain import FakeMaxPainCalculator
from backend.adapters.providers.mock.provider import MockDataProvider
from backend.adapters.providers.mock.walls import FakeWallCalculator
from backend.adapters.storage.memory import InMemoryStorage
from backend.api.serializers import chain_response, gamma_response, websocket_message
from backend.domain.entities import (
    Expiration,
    GammaAggregate,
    GammaAggregateItem,
    InvalidExpirationError,
    InvalidOptionError,
    InvalidStrikeError,
    MarketPrice,
    MarketState,
    OptionGreeks,
    OptionSnapshot,
    OptionType,
    Side,
    dealer_position,
    utc_now,
)
from backend.domain.use_cases import (
    CalculateGammaAggregateUseCase,
    CalculateGammaExposureOrchestrator,
    CalculateGammaFlipUseCase,
    CalculateGreeksUseCase,
    CalculateMaxPainUseCase,
    CalculateWallsUseCase,
    build_market_snapshot,
    calculate_gamma_exposure,
    get_option_chain,
)


def test_dealer_position_is_derived_from_net_gamma() -> None:
    assert dealer_position(Decimal("1")) == "long_gamma"
    assert dealer_position(Decimal("0")) == "long_gamma"
    assert dealer_position(Decimal("-1")) == "short_gamma"


def test_expiration_derives_dte() -> None:
    expiration = Expiration(expiration=date(2026, 8, 21), as_of=date(2026, 7, 22))

    assert expiration.dte == 30


def test_chain_fetch_persists_and_serializes_contract_shape() -> None:
    storage = InMemoryStorage()
    provider = MockDataProvider()

    chain = get_option_chain(storage, provider, "spy")
    payload = chain_response(chain)

    assert payload["schema_version"] == 1
    assert payload["symbol"] == "SPY"
    assert payload["contracts"][0]["expiration"] == chain.contracts[0].expiration.isoformat()


def test_gamma_response_derives_dealer_position() -> None:
    gamma = GammaAggregate(
        symbol="SPY",
        as_of=utc_now(),
        gamma_flip=Decimal("548.5"),
        call_wall=Decimal("555"),
        put_wall=Decimal("540"),
        max_pain=Decimal("550"),
        net_gamma=Decimal("-1250000"),
        dealer_gamma_notional=Decimal("-1250000"),
    )

    payload = gamma_response(gamma)

    assert payload["schema_version"] == 1
    assert payload["dealer_position"] == "short_gamma"
    assert "dealer_gamma_notional" not in payload


def test_calculate_gamma_exposure_orchestrates_and_persists() -> None:
    storage = InMemoryStorage()
    storage.save_chain_snapshot(MockDataProvider().get_option_chain("SPY"))
    exposure = FakeGammaExposureCalculator()
    orchestrator = CalculateGammaExposureOrchestrator(
        storage=storage,
        greeks=CalculateGreeksUseCase(FakeGreeksCalculator()),
        aggregate=CalculateGammaAggregateUseCase(
            exposure, FakeGammaAggregateCalculator()
        ),
        gamma_flip=CalculateGammaFlipUseCase(FakeGammaFlipCalculator()),
        walls=CalculateWallsUseCase(FakeWallCalculator()),
        max_pain=CalculateMaxPainUseCase(FakeMaxPainCalculator()),
    )

    result = calculate_gamma_exposure(orchestrator, "SPY")

    assert storage.get_latest_gamma_aggregate("SPY") is result
    # MockDataProvider's SPY chain gives every strike identical OI/gamma on
    # both legs (see backend/adapters/providers/mock/provider.py's
    # _contract), so call_gamma_exposure == -put_gamma_exposure exactly --
    # net_gamma is 0 at every strike, meaning there's genuinely no
    # directional dealer positioning to build a wall from. FakeWallCalculator
    # correctly returns no wall in that case (see test_walls_engine.py for
    # the real selection logic), and call_wall/put_wall are now properly
    # nullable (2026-09-25 fix) to represent exactly this -- None, not a
    # fake $0 that used to be indistinguishable from a real wall at strike
    # 0. This smoke test only cares that the orchestrator wires walls
    # through to persistence, not what value they land on with this
    # deliberately symmetric fixture.
    assert result.call_wall is None
    assert result.put_wall is None
    assert result.max_pain > 0


def test_get_latest_chain_snapshot_ignores_a_fresher_narrow_write_for_indices() -> None:
    # Confirmed live, 2026-09-18: SPX reporting price above gamma_flip
    # while still showing short_gamma (net_gamma -20B to -32B), and ~1-in-5
    # cycles collapsing to gamma_flip=null/walls=0. Root cause: an unscoped
    # ("give me whatever's latest") read picked up the option chain
    # viewer's narrow single-expiration write over the scheduler's full
    # multi-expiration one, just because it landed with a fresher
    # timestamp -- see PostgreSQLStorage.get_latest_chain_snapshot's own
    # comment for the full mechanism. This is the storage-layer regression
    # test for that fix, independent of the gamma math itself (already
    # covered by test_calculate_gamma_exposure_orchestrates_and_persists).
    storage = InMemoryStorage()
    near = MockDataProvider().get_option_chain("SPX")
    far = MockDataProvider().get_option_chain("SPX", date(2026, 3, 20))
    full = replace(near, contracts=near.contracts + far.contracts)
    storage.save_chain_snapshot(full)

    # The narrow write lands *after* the full one -- exactly the ordering
    # that broke SPX live: a fresher timestamp from a single-expiration
    # fetch (the chain viewer) shadowing the scheduler's own full fetch.
    narrow = replace(near, as_of=full.as_of + timedelta(minutes=1))
    storage.save_chain_snapshot(narrow)

    latest = storage.get_latest_chain_snapshot("SPX")

    assert latest is full
    assert len({contract.expiration for contract in latest.contracts}) > 1


def test_get_latest_chain_snapshot_ignores_a_fresher_narrow_write_for_equities_too() -> None:
    # Confirmed live, 2026-09-24: the exact same race as the SPX incident
    # above, but for AAPL -- an equity, not an index. The 2026-09-18 fix
    # only guarded indices, on the assumption a narrow single-expiration
    # write "only exists for indices in the first place" (see the original
    # comment this replaced in PostgreSQLStorage.get_latest_chain_snapshot).
    # That assumption was wrong: the Volatility Smile panel
    # (frontend/components/volatility-smile.tsx) polls /chain/{symbol}
    # with whatever expiration the user has selected, for ANY symbol, and
    # if that selection goes stale (e.g. an already-expired date left
    # selected across a day rollover) it keeps writing a narrow,
    # ~16-contract snapshot that raced the scheduler's own ~680-contract
    # full write for AAPL every other cycle -- gamma_flip/walls silently
    # went null/0 live. The guard now applies to every symbol, not just
    # indices, so this regression test uses a plain equity on purpose.
    storage = InMemoryStorage()
    near = MockDataProvider().get_option_chain("AAPL")
    far = MockDataProvider().get_option_chain("AAPL", date(2026, 3, 20))
    full = replace(near, contracts=near.contracts + far.contracts)
    storage.save_chain_snapshot(full)

    narrow = replace(near, as_of=full.as_of + timedelta(minutes=1))
    storage.save_chain_snapshot(narrow)

    latest = storage.get_latest_chain_snapshot("AAPL")

    assert latest is full
    assert len({contract.expiration for contract in latest.contracts}) > 1


def test_get_latest_chain_snapshot_still_serves_a_single_expiration_on_explicit_request() -> None:
    # Requirement: the option chain viewer (GET /chain/SPX?expiration=...)
    # must keep working unchanged -- this fix only guards the *unscoped*
    # read the gamma orchestrator makes, never a request for one specific
    # expiration.
    storage = InMemoryStorage()
    near = MockDataProvider().get_option_chain("SPX")
    far = MockDataProvider().get_option_chain("SPX", date(2026, 3, 20))
    full = replace(near, contracts=near.contracts + far.contracts)
    storage.save_chain_snapshot(full)
    narrow = replace(near, as_of=full.as_of + timedelta(minutes=1))
    storage.save_chain_snapshot(narrow)

    latest = storage.get_latest_chain_snapshot("SPX", expiration=narrow.contracts[0].expiration)

    assert latest is narrow


def test_gamma_aggregate_items_round_trip_in_memory() -> None:
    storage = InMemoryStorage()
    aggregate = GammaAggregate(
        symbol="SPY",
        as_of=utc_now(),
        items=(
            GammaAggregateItem(
                strike=Decimal("545"),
                total_gamma_exposure=Decimal("390"),
                call_gamma_exposure=Decimal("240"),
                put_gamma_exposure=Decimal("-150"),
                net_gamma=Decimal("90"),
                contract_count=2,
                absolute_gamma=Decimal("90"),
                open_interest=14000,
                volume=6800,
            ),
            GammaAggregateItem(
                strike=Decimal("550"),
                total_gamma_exposure=Decimal("200"),
                call_gamma_exposure=Decimal("120"),
                put_gamma_exposure=Decimal("-80"),
                net_gamma=Decimal("40"),
                contract_count=3,
                absolute_gamma=Decimal("40"),
            ),
        ),
        gamma_flip=Decimal("548.5"),
        max_pain=Decimal("550"),
        net_gamma=Decimal("-1250000"),
    )

    storage.save_gamma_aggregate(aggregate)
    loaded = storage.get_latest_gamma_aggregate("SPY")

    assert loaded is not None
    assert loaded.items == aggregate.items


def test_market_snapshot_is_projection_not_persisted_table_model() -> None:
    storage = InMemoryStorage()
    now = utc_now()
    storage.save_market_price(
        MarketPrice(symbol="SPY", as_of=now, price=Decimal("552.25"), volume=1000)
    )
    storage.save_gamma_aggregate(
        GammaAggregate(
            symbol="SPY",
            as_of=now,
            gamma_flip=Decimal("550"),
            net_gamma=Decimal("100"),
        )
    )
    storage.save_chain_snapshot(MockDataProvider().get_option_chain("SPY"))

    snapshot = build_market_snapshot(storage, "SPY")

    assert snapshot.symbol == "SPY"
    assert snapshot.price == Decimal("552.25")
    assert snapshot.dealer_mode == "long_gamma"
    assert snapshot.expected_move is not None


def test_websocket_message_always_includes_schema_version() -> None:
    as_of = utc_now() + timedelta(seconds=1)
    payload = websocket_message("gamma", "spy", {"dealer_position": "short_gamma"}, as_of)

    assert payload["schema_version"] == 1
    assert payload["channel"] == "gamma"
    assert payload["symbol"] == "SPY"


def test_domain_model_exposes_required_alias_enums_and_snapshot() -> None:
    contract = MockDataProvider().get_option_chain("spy").contracts[0]
    snapshot = OptionSnapshot(contract=contract, greeks=contract.greeks, as_of=utc_now())

    assert OptionType.CALL.value == "call"
    assert Side.UNKNOWN.value == "unknown"
    assert MarketState.UNKNOWN.value == "unknown"
    assert snapshot.contract.underlying == "SPY"


def test_domain_model_rejects_invalid_contract_strike() -> None:
    contract = MockDataProvider().get_option_chain("spy").contracts[0]

    with pytest.raises(InvalidStrikeError):
        type(contract)(
            underlying=contract.underlying,
            strike=Decimal("0"),
            expiration=contract.expiration,
            contract_type=contract.contract_type,
            occ_symbol=contract.occ_symbol,
            bid=contract.bid,
            ask=contract.ask,
            last=contract.last,
            volume=contract.volume,
            open_interest=contract.open_interest,
            iv=contract.iv,
            greeks=contract.greeks,
        )


def test_domain_model_rejects_expiration_before_as_of() -> None:
    with pytest.raises(InvalidExpirationError):
        Expiration(expiration=date(2026, 7, 21), as_of=date(2026, 7, 22))


def test_domain_model_rejects_invalid_contract_quote_and_greeks() -> None:
    contract = MockDataProvider().get_option_chain("spy").contracts[0]

    with pytest.raises(InvalidOptionError):
        type(contract)(
            underlying=contract.underlying,
            strike=contract.strike,
            expiration=contract.expiration,
            contract_type=contract.contract_type,
            occ_symbol=contract.occ_symbol,
            bid=Decimal("2"),
            ask=Decimal("1"),
            last=Decimal("1.5"),
            volume=1,
            open_interest=1,
            iv=Decimal("0.2"),
            greeks=contract.greeks,
        )

    with pytest.raises(InvalidOptionError):
        OptionGreeks(
            delta=Decimal("1.1"),
            gamma=Decimal("0.01"),
            theta=Decimal("0"),
            vega=Decimal("0"),
            charm=Decimal("0"),
            vanna=Decimal("0"),
        )

    with pytest.raises(InvalidOptionError):
        OptionGreeks(
            delta=Decimal("0.5"),
            gamma=Decimal("NaN"),
            theta=Decimal("0"),
            vega=Decimal("0"),
            charm=Decimal("0"),
            vanna=Decimal("0"),
        )
