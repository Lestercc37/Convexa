from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.api.routes import api_router
from backend.api.serializers import error_response
from backend.core.container import build_container
from backend.core.logging import configure_logging
from backend.domain.use_cases.errors import QllError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # The background systems moved out of this process to their own,
    # per the approved process-split design: UnderlyingRefreshScheduler
    # to backend/scheduler_worker.py, WhaleAlertsStreamManager/
    # UnderlyingPriceStreamManager/StreamStateExporter to
    # backend/worker.py (split further from each other 2026-09-22 --
    # see scheduler_worker.py's own docstring for why). This process
    # only serves HTTP now.
    #
    # Deliberately never calls `await container.market_data_provider.start()`
    # here: that's the ONE place that opens ThetaDataProvider's 3
    # WebSocket connections (Trade/Quote/Underlying-Trade streams) *and*
    # does a real REST contract-discovery burst for every active symbol
    # at startup -- confirmed by reading start()'s actual body, not
    # assumed. This process's ThetaDataProvider instance (built by
    # container.py, same as the worker's -- coordinated through the
    # same Postgres-backed theta_request_slots semaphore, see
    # request_slots.py) only ever needs the plain REST methods
    # (get_option_chain / get_underlying_snapshot / get_daily_bars),
    # used by /chain/{symbol}'s live-fallback and
    # /internal/trigger-calculation -- neither needs the streams.
    #
    # get_option_chain()'s `volume` field comes from `self._stream.
    # cumulative_volume(occ_symbol)`, a dict the trade stream populates
    # as it runs -- with the stream never started, this API-process
    # instance would otherwise always report volume=0 for every contract
    # on any chain it fetches live, silently breaking
    # /internal/trigger-calculation's own RefreshUnderlyingSnapshotUseCase
    # call (WhaleAlertsEngine.process()'s whale/unusual detection is a
    # volume DELTA). Fixed for every caller of execute(), not just this
    # process, by RefreshUnderlyingSnapshotUseCase's own
    # _merge_cumulative_volume -- see that use case's comment and
    # backend/scheduler_worker.py's own docstring (the same gap, but for
    # that process's OWN scheduler cycle, not just this rarely-hit path).
    #
    # `.stop()` IS still called below, even though `.start()` never
    # ran: every stream's own stop() no-ops when its task was never
    # started (`if self._task is None: return`), so this is safe, and
    # it's what actually closes the underlying httpx.Client at
    # shutdown -- skipping it would leak that connection pool.
    container = build_container()
    configure_logging(container.settings)
    app.state.container = container
    logger.info("Starting %s", container.settings.app_name)
    # Real-time chart price push: the Worker NOTIFYs on every MarketPrice
    # it persists (see AsyncPostgreSQLStorage.save_market_price); this
    # process listens and forwards to /ws/market/{symbol} clients. None
    # without a real Postgres behind DATABASE_URL (tests, sqlite) -- see
    # container.py's own comment on price_notification_listener.
    if container.price_notification_listener is not None:
        container.price_notification_listener.start()
    try:
        yield
    finally:
        if container.price_notification_listener is not None:
            await container.price_notification_listener.stop()
        await container.market_data_provider.stop()
        if container.storage_engine is not None:
            container.storage_engine.dispose()
        if container.whale_alerts_storage_engine is not None:
            container.whale_alerts_storage_engine.dispose()
        await container.database_engine.dispose()
        logger.info("Stopping %s", container.settings.app_name)


def create_app() -> FastAPI:
    container = build_container()
    app = FastAPI(
        title=container.settings.app_name,
        version=container.settings.version,
        description="Backend API for QLL Eagle Platform.",
        openapi_url=container.settings.openapi_url,
        docs_url=container.settings.docs_url,
        redoc_url=container.settings.redoc_url,
        lifespan=lifespan,
    )
    app.state.container = container

    @app.exception_handler(QllError)
    async def qll_error_handler(request: Request, error: QllError) -> JSONResponse:
        del request
        status_code = 404 if error.code == "NOT_FOUND" else 500
        return JSONResponse(status_code=status_code, content=error_response(error))

    app.include_router(api_router())
    return app


app = create_app()
