from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from backend.domain.entities import (
    SCHEMA_VERSION,
    ContractType,
    DerivedMetrics,
    FlowEvent,
    GammaAggregate,
    GammaExposure,
    GammaFlip,
    Greeks,
    MarketSnapshot,
    MaxPain,
    OptionChain,
    OptionContract,
    Underlying,
    Walls,
)
from backend.domain.use_cases.errors import QllError


def underlyings_response(items: list[Underlying]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "underlyings": [
            {"symbol": i.symbol, "kind": i.kind.value, "is_priority": i.is_priority} for i in items
        ],
    }


def chain_response(chain: OptionChain) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "symbol": chain.symbol,
        "as_of": _dt(chain.as_of),
        "spot_price": _num(chain.spot_price),
        "contracts": [
            {
                "occ_symbol": contract.occ_symbol,
                "strike": _num(contract.strike),
                "expiration": contract.expiration.isoformat(),
                "type": contract.contract_type.value,
                "bid": _num(contract.bid),
                "ask": _num(contract.ask),
                "iv": _num(contract.iv),
                "delta": _num(contract.greeks.delta),
                "gamma": _num(contract.greeks.gamma),
                "theta": _num(contract.greeks.theta),
                "vega": _num(contract.greeks.vega),
                "charm": _num(contract.greeks.charm),
                "vanna": _num(contract.greeks.vanna),
                "open_interest": contract.open_interest,
                "volume": contract.volume,
            }
            for contract in chain.contracts
        ],
    }


def greeks_chain_response(chain: OptionChain) -> dict[str, Any]:
    payload = chain_response(chain)
    for contract_payload, contract in zip(payload["contracts"], chain.contracts, strict=True):
        contract_payload["theta"] = _num(contract.greeks.theta)
        contract_payload["vega"] = _num(contract.greeks.vega)
        contract_payload["charm"] = _num(contract.greeks.charm)
        contract_payload["vanna"] = _num(contract.greeks.vanna)
    return payload


def gamma_aggregate_response(gamma: GammaAggregate) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "symbol": gamma.symbol,
        "as_of": _dt(gamma.as_of),
        "gamma_flip": _num(gamma.gamma_flip),
        "max_pain": _num(gamma.max_pain),
        "total_market_gamma": _num(gamma.total_market_gamma),
        "positive_gamma": _num(gamma.positive_gamma),
        "negative_gamma": _num(gamma.negative_gamma),
        "absolute_gamma_strike": _num(gamma.absolute_gamma_strike),
        "peak_gamma_value": _num(gamma.peak_gamma_value),
        "items": [
            {
                "strike": _num(item.strike),
                "total_gamma_exposure": _num(item.total_gamma_exposure),
                "call_gamma_exposure": _num(item.call_gamma_exposure),
                "put_gamma_exposure": _num(item.put_gamma_exposure),
                "net_gamma": _num(item.net_gamma),
                "contract_count": item.contract_count,
                "absolute_gamma": _num(item.absolute_gamma),
            }
            for item in gamma.items
        ],
    }


def gamma_exposure_response(items: tuple[GammaExposure, ...]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "items": [
            {
                "occ_symbol": item.occ_symbol,
                "strike": _num(item.strike),
                "contract_type": item.contract_type.value,
                "expiration": item.expiration.isoformat(),
                "gamma": _num(item.gamma),
                "open_interest": item.open_interest,
                "dealer_gamma_exposure": _num(item.dealer_gamma_exposure),
                "sign": _num(item.sign),
            }
            for item in items
        ],
    }


def walls_response(walls: Walls) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "symbol": walls.symbol,
        "as_of": _dt(walls.as_of),
        "call_wall": _wall_response(walls.call_wall),
        "put_wall": _wall_response(walls.put_wall),
    }


def gamma_flip_response(flip: GammaFlip) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "gamma_flip_price": _optional_num(flip.gamma_flip_price),
        "lower_strike": _optional_num(flip.lower_strike),
        "upper_strike": _optional_num(flip.upper_strike),
        "lower_gamma": _optional_num(flip.lower_gamma),
        "upper_gamma": _optional_num(flip.upper_gamma),
        "interpolation_ratio": _optional_num(flip.interpolation_ratio),
        "flip_found": flip.flip_found,
    }


def max_pain_response(max_pain: MaxPain) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "symbol": max_pain.symbol,
        "as_of": _dt(max_pain.as_of),
        "max_pain_strike": _num(max_pain.max_pain_strike),
        "total_call_pain": _num(max_pain.total_call_pain),
        "total_put_pain": _num(max_pain.total_put_pain),
        "total_pain": _num(max_pain.total_pain),
        "ranking": [
            {
                "strike": _num(item.strike),
                "total_call_pain": _num(item.total_call_pain),
                "total_put_pain": _num(item.total_put_pain),
                "total_pain": _num(item.total_pain),
            }
            for item in max_pain.ranking
        ],
    }


def option_chain_from_payload(payload: dict[str, Any]) -> OptionChain:
    return OptionChain(
        symbol=str(payload["symbol"]),
        as_of=_parse_datetime(str(payload["as_of"])),
        spot_price=Decimal(str(payload["spot_price"])),
        contracts=tuple(
            _contract_from_payload(str(payload["symbol"]), item)
            for item in payload.get("contracts", ())
        ),
    )


def gamma_response(
    gamma: GammaAggregate,
    derived_metrics: DerivedMetrics | None = None,
) -> dict[str, Any]:
    response = {
        "schema_version": SCHEMA_VERSION,
        "symbol": gamma.symbol,
        "as_of": _dt(gamma.as_of),
        "gamma_flip": _num(gamma.gamma_flip),
        "call_wall": _num(gamma.call_wall),
        "put_wall": _num(gamma.put_wall),
        "absolute_gamma_strike": _num(gamma.absolute_gamma_strike),
        "max_pain": _num(gamma.max_pain),
        "net_gamma": _num(gamma.net_gamma),
        "vega_exposure": _num(gamma.vega_exposure),
        "theta_exposure": _num(gamma.theta_exposure),
        "charm_exposure": _num(gamma.charm_exposure),
        "vanna_exposure": _num(gamma.vanna_exposure),
        "dealer_position": gamma.dealer_position,
    }
    if derived_metrics is not None:
        response["derived_metrics"] = derived_metrics_response(derived_metrics)
    return response


def derived_metrics_response(metrics: DerivedMetrics) -> dict[str, Any]:
    return {
        "dealer_impact_score": {
            "value": _optional_num(metrics.dealer_impact_score.value),
            "provisional": metrics.dealer_impact_score.provisional,
            "days_accumulated": metrics.dealer_impact_score.days_accumulated,
        },
        "signal_alignment_score": {
            "value": _optional_num(metrics.signal_alignment_score.value),
            "provisional": metrics.signal_alignment_score.provisional,
            "days_accumulated": metrics.signal_alignment_score.days_accumulated,
        },
        "market_bias": {
            "score": _optional_num(metrics.market_bias.score),
            "label": metrics.market_bias.label,
            "provisional": metrics.market_bias.provisional,
            "days_accumulated": metrics.market_bias.days_accumulated,
        },
        "volatility_regime": {
            "iv_rank": _optional_num(metrics.volatility_regime.iv_rank),
            "label": metrics.volatility_regime.label,
            "provisional": metrics.volatility_regime.provisional,
            "days_accumulated": metrics.volatility_regime.days_accumulated,
        },
    }


def gamma_history_response(symbol: str, items: list[GammaAggregate]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol.upper(),
        "items": [gamma_response(item) for item in items],
    }


def market_response(snapshot: MarketSnapshot) -> dict[str, Any]:
    gamma = snapshot.gamma
    if gamma is None:
        raise ValueError("market snapshot requires a gamma aggregate")
    expected_move = snapshot.expected_move
    if expected_move is None:
        raise ValueError("market snapshot requires expected move")
    anchored_vwap = snapshot.anchored_vwap
    if anchored_vwap is None:
        raise ValueError("market snapshot requires anchored vwap")
    atr_range = snapshot.atr_range
    if atr_range is None:
        raise ValueError("market snapshot requires atr range")
    return {
        "schema_version": SCHEMA_VERSION,
        "symbol": snapshot.symbol,
        "as_of": _dt(snapshot.as_of),
        "price": _num(snapshot.price),
        "volume": snapshot.volume,
        "gamma_flip": _num(gamma.gamma_flip),
        "call_wall": _num(gamma.call_wall),
        "put_wall": _num(gamma.put_wall),
        "absolute_gamma_strike": _num(gamma.absolute_gamma_strike),
        "dealer_mode": snapshot.dealer_mode,
        "dealer_mode_source": snapshot.dealer_mode_source,
        "dealer_mode_confirmed": snapshot.dealer_mode_confirmed,
        "gamma_as_of": _dt(snapshot.gamma_as_of),
        "expected_move": {
            "implied_1sd_dollars": _num(expected_move.implied_1sd_dollars),
            "implied_1sd_pct": _num(expected_move.implied_1sd_pct),
            "remaining_1sd_dollars": _num(expected_move.remaining_1sd_dollars),
            "remaining_1sd_pct": _num(expected_move.remaining_1sd_pct),
            "upper_bound": _num(expected_move.upper_bound),
            "lower_bound": _num(expected_move.lower_bound),
            "atm_iv": _num(expected_move.atm_iv),
        },
        "anchored_vwap": {
            "value": _optional_num(anchored_vwap.value),
            "provisional": anchored_vwap.provisional,
            "anchor_time": _dt(anchored_vwap.anchor_time),
            "sample_count": anchored_vwap.sample_count,
        },
        "atr_range": {
            "atr": _optional_num(atr_range.atr),
            "atr_provisional": atr_range.atr_provisional,
            "daily_bars_count": atr_range.daily_bars_count,
            "today_open": _optional_num(atr_range.today_open),
            "bands_provisional": atr_range.bands_provisional,
            "outer_upper_band": _optional_num(atr_range.outer_upper_band),
            "outer_lower_band": _optional_num(atr_range.outer_lower_band),
            "inner_upper_band": _optional_num(atr_range.inner_upper_band),
            "inner_lower_band": _optional_num(atr_range.inner_lower_band),
        },
    }


def flow_response(symbol: str, events: list[FlowEvent]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol.upper(),
        "events": [
            {
                "as_of": _dt(e.as_of),
                "occ_symbol": e.occ_symbol,
                "event_type": e.event_type.value,
                "premium": _num(e.premium),
                "size": e.size,
                "aggressor_side": e.aggressor_side.value,
            }
            for e in events
        ],
    }


def websocket_message(
    channel: str, symbol: str, payload: dict[str, Any], as_of: datetime
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "channel": channel,
        "type": "update",
        "symbol": symbol.upper(),
        "payload": payload,
        "as_of": _dt(as_of),
    }


def error_response(error: QllError) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "error": {"code": error.code, "message": str(error)}}


def _wall_response(wall: Any) -> dict[str, Any] | None:
    if wall is None:
        return None
    return {
        "strike": _num(wall.strike),
        "gamma": _num(wall.gamma),
        "open_interest": wall.open_interest,
        "volume": wall.volume,
    }


def _contract_from_payload(default_symbol: str, payload: dict[str, Any]) -> OptionContract:
    contract_type = ContractType(str(payload.get("type") or payload.get("contract_type")))
    greeks_payload = payload.get("greeks", payload)
    return OptionContract(
        underlying=str(payload.get("underlying", default_symbol)),
        strike=Decimal(str(payload["strike"])),
        expiration=date.fromisoformat(str(payload["expiration"])),
        contract_type=contract_type,
        occ_symbol=str(payload["occ_symbol"]),
        bid=Decimal(str(payload["bid"])),
        ask=Decimal(str(payload["ask"])),
        last=Decimal(str(payload.get("last", payload["bid"]))),
        volume=int(payload["volume"]),
        open_interest=int(payload["open_interest"]),
        iv=Decimal(str(payload["iv"])),
        greeks=Greeks(
            delta=Decimal(str(greeks_payload.get("delta", "0"))),
            gamma=Decimal(str(greeks_payload.get("gamma", "0"))),
            theta=Decimal(str(greeks_payload["theta"])),
            vega=Decimal(str(greeks_payload["vega"])),
            charm=Decimal(str(greeks_payload["charm"])),
            vanna=Decimal(str(greeks_payload["vanna"])),
        ),
    )


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _dt(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _num(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _optional_num(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    return _num(value)
