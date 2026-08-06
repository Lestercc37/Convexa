from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.domain.entities import (
    ContractType,
    GammaAggregate,
    GammaAggregateItem,
    Greeks,
    OptionChain,
    OptionContract,
)

Number = int | float


class ExpectedMoveResponse(BaseModel):
    implied_1sd_dollars: Number
    implied_1sd_pct: Number
    remaining_1sd_dollars: Number
    remaining_1sd_pct: Number
    upper_bound: Number
    lower_bound: Number
    atm_iv: Number


class MarketSnapshotResponse(BaseModel):
    schema_version: int = Field(examples=[1])
    symbol: str = Field(examples=["SPY"])
    as_of: str = Field(examples=["2026-01-15T14:30:00Z"])
    price: Number = Field(examples=[552.25])
    volume: int = Field(examples=[1250000])
    gamma_flip: Number = Field(examples=[548.5])
    call_wall: Number = Field(examples=[555])
    put_wall: Number = Field(examples=[540])
    absolute_gamma_strike: Number = Field(examples=[550])
    dealer_mode: Literal["long_gamma", "short_gamma"]
    dealer_mode_source: Literal["agree", "price_vs_flip"]
    dealer_mode_confirmed: bool
    gamma_as_of: str = Field(examples=["2026-01-15T14:29:30Z"])
    expected_move: ExpectedMoveResponse


class OptionContractResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    occ_symbol: str = Field(examples=["SPY260220C00540000"])
    strike: Number = Field(examples=[540])
    expiration: date = Field(examples=["2026-02-20"])
    contract_type: Literal["call", "put"] = Field(alias="type", examples=["call"])
    bid: Number = Field(examples=[1.2])
    ask: Number = Field(examples=[1.25])
    iv: Number = Field(examples=[0.18])
    delta: Number = Field(examples=[0.42])
    gamma: Number = Field(examples=[0.03])
    theta: Number = Field(examples=[-0.015])
    vega: Number = Field(examples=[0.12])
    charm: Number = Field(examples=[-0.001])
    vanna: Number = Field(examples=[0.02])
    open_interest: int = Field(examples=[8000])
    volume: int = Field(examples=[3400])


class OptionChainResponse(BaseModel):
    schema_version: int = Field(examples=[1])
    symbol: str = Field(examples=["SPY"])
    as_of: str = Field(examples=["2026-01-15T14:30:00Z"])
    spot_price: Number = Field(examples=[552.25])
    contracts: list[OptionContractResponse]


class EagleAlertResponse(BaseModel):
    symbol: str
    contract: str
    type: Literal["WHALE", "UNUSUAL"]
    amount: Number
    timestamp: str


class EagleAlertsResponse(BaseModel):
    schema_version: int = Field(examples=[1])
    symbol: str
    alerts: list[EagleAlertResponse]


class ScreenerPresetItemResponse(BaseModel):
    symbol: str
    as_of: str
    contract: str | None = None
    alert_type: Literal["WHALE", "UNUSUAL"] | None = None
    amount: Number | None = None
    net_gamma: Number | None = None
    gamma_flip: Number | None = None
    call_wall: Number | None = None
    put_wall: Number | None = None
    max_pain: Number | None = None
    vanna_exposure: Number | None = None
    charm_exposure: Number | None = None


class ScreenerPresetResponse(BaseModel):
    schema_version: int = Field(examples=[1])
    preset: Literal[
        "unusual-options-activity",
        "negative-gamma-board",
        "max-pain-key-levels",
        "vanna-exposure-leaders",
        "charm-decay-pressure",
    ]
    results: list[ScreenerPresetItemResponse]


class GreeksContractResponse(OptionContractResponse):
    pass


class GreeksResponse(BaseModel):
    schema_version: int = Field(examples=[1])
    symbol: str = Field(examples=["SPY"])
    as_of: str = Field(examples=["2026-01-15T14:30:00Z"])
    spot_price: Number = Field(examples=[552.25])
    contracts: list[GreeksContractResponse]


class GammaExposureItemResponse(BaseModel):
    occ_symbol: str = Field(examples=["SPY260220C00540000"])
    strike: Number = Field(examples=[540])
    contract_type: Literal["call", "put"] = Field(examples=["call"])
    expiration: date = Field(examples=["2026-02-20"])
    gamma: Number = Field(examples=[0.03])
    open_interest: int = Field(examples=[8000])
    dealer_gamma_exposure: Number = Field(examples=[17820000])
    sign: Number = Field(examples=[1])


class GammaExposureResponse(BaseModel):
    schema_version: int = Field(examples=[1])
    items: list[GammaExposureItemResponse]


class GammaAggregateItemResponse(BaseModel):
    strike: Number = Field(examples=[540])
    total_gamma_exposure: Number = Field(examples=[390])
    call_gamma_exposure: Number = Field(examples=[240])
    put_gamma_exposure: Number = Field(examples=[-150])
    net_gamma: Number = Field(examples=[90])
    contract_count: int = Field(examples=[2])
    absolute_gamma: Number = Field(examples=[90])
    open_interest: int = Field(default=0, ge=0, examples=[14000])
    volume: int = Field(default=0, ge=0, examples=[6800])


GammaAggregateStrikeResponse = GammaAggregateItemResponse


class GammaAggregateResponse(BaseModel):
    schema_version: int = Field(examples=[1])
    symbol: str = Field(examples=["SPY"])
    as_of: str = Field(examples=["2026-01-15T14:30:00Z"])
    total_market_gamma: Number = Field(examples=[280])
    positive_gamma: Number = Field(examples=[280])
    negative_gamma: Number = Field(examples=[0])
    absolute_gamma_strike: Number = Field(examples=[545])
    peak_gamma_value: Number = Field(examples=[190])
    items: list[GammaAggregateItemResponse]


class DerivedMetricValueResponse(BaseModel):
    value: Number | None
    provisional: bool
    days_accumulated: int


class MarketBiasResponse(BaseModel):
    score: Number | None
    label: Literal["bullish", "bearish", "neutral"] | None
    provisional: bool
    days_accumulated: int


class VolatilityRegimeResponse(BaseModel):
    iv_rank: Number | None
    label: Literal["low", "moderate", "high"] | None
    provisional: bool
    days_accumulated: int


class DerivedMetricsResponse(BaseModel):
    dealer_impact_score: DerivedMetricValueResponse
    signal_alignment_score: DerivedMetricValueResponse
    market_bias: MarketBiasResponse
    volatility_regime: VolatilityRegimeResponse


class GammaSummaryResponse(BaseModel):
    schema_version: int = Field(examples=[1])
    symbol: str = Field(examples=["SPY"])
    as_of: str = Field(examples=["2026-01-15T14:30:00Z"])
    gamma_flip: Number = Field(examples=[548.5])
    call_wall: Number = Field(examples=[555])
    put_wall: Number = Field(examples=[540])
    absolute_gamma_strike: Number = Field(examples=[550])
    max_pain: Number = Field(examples=[550])
    net_gamma: Number = Field(examples=[-1250000])
    vega_exposure: Number = Field(examples=[875000])
    theta_exposure: Number = Field(examples=[-420000])
    charm_exposure: Number = Field(examples=[125000])
    vanna_exposure: Number = Field(examples=[250000])
    dealer_position: Literal["long_gamma", "short_gamma"]


class GammaResponse(GammaSummaryResponse):
    derived_metrics: DerivedMetricsResponse


class GammaHistoryResponse(BaseModel):
    schema_version: int = Field(examples=[1])
    symbol: str = Field(examples=["SPY"])
    items: list[GammaSummaryResponse]


class UnderlyingResponse(BaseModel):
    symbol: str
    kind: Literal["equity", "index"]
    is_priority: bool


class UnderlyingsResponse(BaseModel):
    schema_version: int = Field(examples=[1])
    underlyings: list[UnderlyingResponse]


class FlowEventResponse(BaseModel):
    as_of: str
    occ_symbol: str
    event_type: Literal["sweep", "block", "unusual"]
    premium: Number
    size: int
    aggressor_side: Literal["buy", "sell", "unknown"]


class FlowResponse(BaseModel):
    schema_version: int = Field(examples=[1])
    symbol: str
    events: list[FlowEventResponse]


class TriggerCalculationResponse(BaseModel):
    schema_version: int = Field(examples=[1])
    symbol: str
    status: Literal["calculated"]
    gamma: GammaResponse


class GammaFlipRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "symbol": "SPY",
                "as_of": "2026-01-15T14:30:00Z",
                "items": [
                    {
                        "strike": 540,
                        "total_gamma_exposure": 390,
                        "call_gamma_exposure": 240,
                        "put_gamma_exposure": 150,
                        "net_gamma": 90,
                        "contract_count": 2,
                        "absolute_gamma": 90,
                    },
                    {
                        "strike": 545,
                        "total_gamma_exposure": 210,
                        "call_gamma_exposure": 200,
                        "put_gamma_exposure": 10,
                        "net_gamma": -10,
                        "contract_count": 2,
                        "absolute_gamma": 10,
                    },
                ],
            }
        }
    )

    symbol: str = Field(min_length=1, examples=["SPY"])
    as_of: datetime = Field(examples=["2026-01-15T14:30:00Z"])
    items: list[GammaAggregateItemResponse] = Field(min_length=1)

    def to_domain(self) -> GammaAggregate:
        return GammaAggregate(
            symbol=self.symbol,
            as_of=self.as_of,
            items=tuple(
                GammaAggregateItem(
                    strike=Decimal(str(item.strike)),
                    total_gamma_exposure=Decimal(str(item.total_gamma_exposure)),
                    call_gamma_exposure=Decimal(str(item.call_gamma_exposure)),
                    put_gamma_exposure=Decimal(str(item.put_gamma_exposure)),
                    net_gamma=Decimal(str(item.net_gamma)),
                    contract_count=item.contract_count,
                    absolute_gamma=Decimal(str(item.absolute_gamma)),
                    open_interest=item.open_interest,
                    volume=item.volume,
                )
                for item in self.items
            ),
        )


class WallResponse(BaseModel):
    strike: Number = Field(examples=[545])
    gamma: Number = Field(examples=[200])
    open_interest: int = Field(examples=[6000])
    volume: int = Field(examples=[4200])


class WallsResponse(BaseModel):
    schema_version: int = Field(examples=[1])
    symbol: str = Field(examples=["SPY"])
    as_of: str = Field(examples=["2026-01-15T14:30:00Z"])
    call_wall: WallResponse | None = None
    put_wall: WallResponse | None = None


class GammaFlipResponse(BaseModel):
    schema_version: int = Field(examples=[1])
    gamma_flip_price: Number | None = Field(default=None, examples=[544.5])
    lower_strike: Number | None = Field(default=None, examples=[540])
    upper_strike: Number | None = Field(default=None, examples=[545])
    lower_gamma: Number | None = Field(default=None, examples=[90])
    upper_gamma: Number | None = Field(default=None, examples=[-10])
    interpolation_ratio: Number | None = Field(default=None, examples=[0.9])
    flip_found: bool = Field(examples=[True])


class MaxPainStrikePainResponse(BaseModel):
    strike: Number = Field(examples=[540])
    total_call_pain: Number = Field(examples=[12500])
    total_put_pain: Number = Field(examples=[9000])
    total_pain: Number = Field(examples=[21500])


class MaxPainResponse(BaseModel):
    schema_version: int = Field(examples=[1])
    symbol: str = Field(examples=["SPY"])
    as_of: str = Field(examples=["2026-01-15T14:30:00Z"])
    max_pain_strike: Number = Field(examples=[540])
    total_call_pain: Number = Field(examples=[12500])
    total_put_pain: Number = Field(examples=[9000])
    total_pain: Number = Field(examples=[21500])
    ranking: list[MaxPainStrikePainResponse] = Field(max_length=5)


def _optional_decimal(value: Number | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


class OptionContractRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    occ_symbol: str = Field(min_length=1, examples=["SPY260220C00540000"])
    underlying: str | None = Field(default=None, examples=["SPY"])
    strike: Decimal = Field(gt=0, examples=[540])
    expiration: date = Field(examples=["2026-02-20"])
    contract_type: Literal["call", "put"] = Field(alias="type", examples=["call"])
    bid: Decimal = Field(ge=0, examples=[1.2])
    ask: Decimal = Field(ge=0, examples=[1.25])
    last: Decimal | None = Field(default=None, ge=0, examples=[1.22])
    iv: Decimal = Field(ge=0, examples=[0.18])
    delta: Decimal = Field(default=Decimal("0"), ge=-1, le=1, examples=[0])
    gamma: Decimal = Field(default=Decimal("0"), examples=[0.03])
    theta: Decimal = Field(examples=[-0.015])
    vega: Decimal = Field(examples=[0.12])
    charm: Decimal = Field(examples=[-0.001])
    vanna: Decimal = Field(examples=[0.02])
    open_interest: int = Field(ge=0, examples=[8000])
    volume: int = Field(ge=0, examples=[3400])

    @model_validator(mode="after")
    def validate_quote(self) -> "OptionContractRequest":
        if self.ask < self.bid:
            raise ValueError("ask cannot be lower than bid")
        return self

    def to_domain(self, default_symbol: str) -> OptionContract:
        return OptionContract(
            underlying=self.underlying or default_symbol,
            strike=self.strike,
            expiration=self.expiration,
            contract_type=ContractType(self.contract_type),
            occ_symbol=self.occ_symbol,
            bid=self.bid,
            ask=self.ask,
            last=self.last if self.last is not None else self.bid,
            volume=self.volume,
            open_interest=self.open_interest,
            iv=self.iv,
            greeks=Greeks(
                delta=self.delta,
                gamma=self.gamma,
                theta=self.theta,
                vega=self.vega,
                charm=self.charm,
                vanna=self.vanna,
            ),
        )


class OptionChainRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "symbol": "SPY",
                    "as_of": "2026-01-15T14:30:00Z",
                    "spot_price": 552.25,
                    "contracts": [
                        {
                            "occ_symbol": "SPY260220C00540000",
                            "underlying": "SPY",
                            "strike": 540,
                            "expiration": "2026-02-20",
                            "type": "call",
                            "bid": 1.2,
                            "ask": 1.25,
                            "last": 1.22,
                            "iv": 0.18,
                            "delta": 0,
                            "gamma": 0.03,
                            "theta": -0.015,
                            "vega": 0.12,
                            "charm": -0.001,
                            "vanna": 0.02,
                            "open_interest": 8000,
                            "volume": 3400,
                        }
                    ],
                }
            ],
            "example": {
                "symbol": "SPY",
                "as_of": "2026-01-15T14:30:00Z",
                "spot_price": 552.25,
                "contracts": [
                    {
                        "occ_symbol": "SPY260220C00540000",
                        "underlying": "SPY",
                        "strike": 540,
                        "expiration": "2026-02-20",
                        "type": "call",
                        "bid": 1.2,
                        "ask": 1.25,
                        "last": 1.22,
                        "iv": 0.18,
                        "delta": 0,
                        "gamma": 0.03,
                        "theta": -0.015,
                        "vega": 0.12,
                        "charm": -0.001,
                        "vanna": 0.02,
                        "open_interest": 8000,
                        "volume": 3400,
                    }
                ],
            },
        }
    )

    symbol: str = Field(min_length=1, examples=["SPY"])
    as_of: datetime = Field(examples=["2026-01-15T14:30:00Z"])
    spot_price: Decimal = Field(gt=0, examples=[552.25])
    contracts: list[OptionContractRequest] = Field(min_length=1)

    def to_domain(self) -> OptionChain:
        return OptionChain(
            symbol=self.symbol,
            as_of=self.as_of,
            spot_price=self.spot_price,
            contracts=tuple(contract.to_domain(self.symbol) for contract in self.contracts),
        )
