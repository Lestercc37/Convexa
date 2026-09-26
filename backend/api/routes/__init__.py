from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.deps import require_admin, require_session
from backend.api.routes.alerts import router as alerts_router
from backend.api.routes.auth import router as auth_router
from backend.api.routes.health import router as health_router
from backend.api.routes.internal import router as internal_router
from backend.api.routes.market import router as market_router
from backend.api.routes.market_stream import router as market_stream_router
from backend.api.routes.options import router as options_router
from backend.api.routes.screener_presets import router as screener_presets_router
from backend.api.routes.whale_thresholds import router as whale_thresholds_router


def api_router() -> APIRouter:
    router = APIRouter()
    router.include_router(health_router)
    router.include_router(auth_router, prefix="/api/v1")
    # Every other router below requires a logged-in session -- see
    # backend/api/deps.py's own docstring on why this is applied here
    # (the composition root) rather than per router file, so a route
    # added to an already-gated router can't come back unauthenticated
    # by omission. internal_router's own trigger-calculation route is
    # the one exception that needs require_admin outright (its entire
    # purpose is mutating state), not just require_session.
    router.include_router(
        internal_router, dependencies=[Depends(require_admin)]
    )
    router.include_router(
        market_router, prefix="/api/v1", dependencies=[Depends(require_session)]
    )
    router.include_router(
        market_stream_router, prefix="/api/v1", dependencies=[Depends(require_session)]
    )
    router.include_router(
        options_router, prefix="/api/v1", dependencies=[Depends(require_session)]
    )
    router.include_router(
        alerts_router, prefix="/api/v1", dependencies=[Depends(require_session)]
    )
    # These two also carry a PATCH route each, additionally gated with
    # require_admin directly on that route's own decorator (see
    # screener_presets.py/whale_thresholds.py) -- their GET routes stay
    # readable by every logged-in teammate.
    router.include_router(
        screener_presets_router, prefix="/api/v1", dependencies=[Depends(require_session)]
    )
    router.include_router(
        whale_thresholds_router, prefix="/api/v1", dependencies=[Depends(require_session)]
    )
    return router
