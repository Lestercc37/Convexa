from __future__ import annotations

from fastapi import APIRouter, Request

from backend.api.schemas import (
    WhaleThresholdResponse,
    WhaleThresholdsResponse,
    WhaleThresholdUpdateRequest,
)
from backend.core.container import Container
from backend.domain.entities import SCHEMA_VERSION, WhaleThreshold
from backend.domain.underlyings import ACTIVE_UNDERLYINGS_BY_SYMBOL
from backend.domain.use_cases.errors import NotFoundError

router = APIRouter(tags=["whale-thresholds"])


def _threshold_dict(threshold: WhaleThreshold) -> dict[str, object]:
    return {
        "symbol": threshold.symbol,
        "unusual_min": threshold.unusual_min,
        "whale_min": threshold.whale_min,
        "unusual_multiplier": threshold.unusual_multiplier,
        "whale_multiplier": threshold.whale_multiplier,
        "sustained_flow_min": threshold.sustained_flow_min,
    }


@router.get("/whale-thresholds", response_model=WhaleThresholdsResponse)
def list_whale_thresholds(request: Request) -> WhaleThresholdsResponse:
    container: Container = request.app.state.container
    persisted = container.storage.get_whale_thresholds()
    return WhaleThresholdsResponse.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "thresholds": [
                _threshold_dict(persisted[symbol])
                for symbol in sorted(persisted)
            ],
        }
    )


# The one write endpoint in an otherwise read-only API: Whale Alerts
# thresholds are operator-tuned calibration values (docs/use-cases.md
# already documents them as "defaults... need recalibration with real
# data, not presupposed valid across symbols") — editing them is
# adjusting how the (read-only) alerting engine classifies future
# activity, not writing application data through the API. Piece 1 of
# this PR made WhaleAlertsEngine read thresholds live from storage on
# every process() call specifically so this endpoint has an effect
# without a restart.
@router.patch("/whale-thresholds/{symbol}", response_model=WhaleThresholdResponse)
def update_whale_threshold(
    symbol: str,
    body: WhaleThresholdUpdateRequest,
    request: Request,
) -> WhaleThresholdResponse:
    normalized = symbol.upper()
    if normalized not in ACTIVE_UNDERLYINGS_BY_SYMBOL:
        raise NotFoundError(f"Unknown underlying: {normalized}")

    container: Container = request.app.state.container
    threshold = WhaleThreshold(
        symbol=normalized,
        unusual_min=body.unusual_min,
        whale_min=body.whale_min,
        unusual_multiplier=body.unusual_multiplier,
        whale_multiplier=body.whale_multiplier,
        sustained_flow_min=body.sustained_flow_min,
    )
    container.storage.save_whale_threshold(threshold)
    return WhaleThresholdResponse.model_validate(_threshold_dict(threshold))
