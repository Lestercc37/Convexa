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
    assert result.call_wall > 0
    assert result.put_wall > 0
    assert result.max_pain > 0


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

    snapshot = build_market_snapshot(storage, "SPY")

    assert snapshot.symbol == "SPY"
    assert snapshot.price == Decimal("552.25")
    assert snapshot.dealer_mode == "long_gamma"


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
