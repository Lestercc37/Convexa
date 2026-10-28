"""Standalone entrypoint for the 10-second fast gamma layer (docs/diseno-capa-rapida-gamma-2026-10-19.txt).

Its own OS process, like backend/scheduler_worker.py and backend/whale_alerts_worker.py: the pass is CPU-light (about 0.2 s for four symbols) but it must never share a GIL with the process that
owns the ThetaData WebSocket (the 2026-09-22 reconnect-storm lesson). It never starts the market data provider (no stream, no contract discovery): it reads the chain, the daily bars and the
live price the other processes already store, calls the provider only for the cached risk-free rate and the holiday calendar, and writes only gamma_fast_items (migration 0047).

OFF by default: QLL_FAST_GAMMA_ENABLED=true turns it on, the reverse turns it off, both need a start of this process and nothing else.

Run with:
    python -m backend.fast_gamma_worker
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, date, datetime

from backend.adapters.providers.thetadata.provider import _time_to_expiration_years
from backend.core.container import build_container
from backend.core.logging import configure_logging
from backend.domain.use_cases import is_market_open
from backend.domain.use_cases.fast_gamma import FastGammaLayer
from backend.domain.use_cases.market_hours import EASTERN_TIME

logger = logging.getLogger(__name__)

SUMMARY_EVERY_SECONDS = 300


async def run() -> None:
    container = build_container()
    configure_logging(container.settings, log_file=container.settings.fast_gamma_log_file or "logs/fast_gamma_worker.log")
    settings = container.settings
    logger.info("Starting %s fast gamma worker", settings.app_name)
    if not settings.fast_gamma_enabled:
        logger.warning("fast_gamma_enabled is False -- fast gamma worker has nothing to do, exiting")
        return
    provider = container.market_data_provider
    risk_free_rate = getattr(provider, "risk_free_rate", None)
    if risk_free_rate is None:
        logger.error("the %s provider has no risk_free_rate(); the fast layer only runs on ThetaData -- exiting", settings.data_provider)
        return
    symbols = [symbol.strip().upper() for symbol in settings.fast_gamma_symbols.split(",") if symbol.strip()]
    layer = FastGammaLayer(
        container.storage,
        container.calculate_gamma_exposure_orchestrator,
        time_to_expiration=_time_to_expiration_years,
        risk_free_rate=risk_free_rate,
        itm_iv_from_twin=settings.thetadata_itm_iv_from_twin,
    )
    logger.info("Fast gamma worker running: symbols=%s interval=%.1fs", symbols, settings.fast_gamma_interval_seconds)

    holidays: dict[date, object] = {}
    holidays_year = 0
    rows_since_summary = 0
    passes_since_summary = 0
    errors_since_summary = 0
    slowest = 0.0
    last_summary = time.monotonic()
    try:
        while True:
            started = time.monotonic()
            now = datetime.now(UTC)
            year = now.astimezone(EASTERN_TIME).year
            if year != holidays_year:
                try:
                    calendar = await asyncio.to_thread(provider.get_market_holidays, year)
                    holidays = {holiday.date: holiday for holiday in calendar}
                    holidays_year = year
                except Exception:
                    logger.exception("holiday calendar unavailable, treating the day as a normal weekday")
            if is_market_open(now, holidays):
                for symbol in symbols:
                    symbol_started = time.monotonic()
                    try:
                        rows_since_summary += await asyncio.to_thread(layer.run_pass, symbol)
                        passes_since_summary += 1
                    except Exception:
                        errors_since_summary += 1
                        logger.exception("%s: fast gamma pass failed", symbol)
                    slowest = max(slowest, time.monotonic() - symbol_started)
            if time.monotonic() - last_summary >= SUMMARY_EVERY_SECONDS:
                logger.info(
                    "fast gamma: %d passes, %d rows written, %d errors, slowest single pass %.2fs (last %ds)",
                    passes_since_summary,
                    rows_since_summary,
                    errors_since_summary,
                    slowest,
                    SUMMARY_EVERY_SECONDS,
                )
                rows_since_summary = passes_since_summary = errors_since_summary = 0
                slowest = 0.0
                last_summary = time.monotonic()
            await asyncio.sleep(max(0.0, settings.fast_gamma_interval_seconds - (time.monotonic() - started)))
    finally:
        logger.info("Stopping %s fast gamma worker", settings.app_name)
        await provider.stop()
        if container.storage_engine is not None:
            container.storage_engine.dispose()
        await container.database_engine.dispose()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
