"""The Worker's periodic reconcile() can be switched off (Settings.thetadata_reconcile_enabled, OFF by default).

Why: one REST ohlc call per registered contract every 20 minutes (1,200-1,800 calls, 13-25 s, up to 8 request slots at once), comparing REST volume with a
stream counter the Worker no longer keeps (so it only logs "stream=0" mismatches). It starved the scheduler of request slots on 2026-10-06 10:21 and
2026-10-07 13:50. Reverting = QLL_THETADATA_RECONCILE_ENABLED=true + restart the Worker (no code revert needed)."""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import httpx
import pytest

import backend.adapters.providers.thetadata.provider as provider_module
import backend.core.container as container_module
from backend.adapters.providers.thetadata.provider import ThetaDataProvider, ThetaStreamHub
from backend.core.settings import Settings
from backend.domain.entities import ContractType

REST_URL = "http://thetaterminal.test"
WS_URL = "ws://thetaterminal.test/v1/events"


def _hub(*, enabled: bool | None, calls: list[str]) -> ThetaStreamHub:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(472, json={})        # "no data": the normal answer for an untraded contract

    client = httpx.Client(base_url=REST_URL, transport=httpx.MockTransport(handler))
    hub = ThetaStreamHub(WS_URL, client) if enabled is None else ThetaStreamHub(WS_URL, client, reconcile_enabled=enabled)
    hub.register_contract("SPXW261007C07800000", "SPXW", date(2026, 10, 7), ContractType.CALL, Decimal("7800"))
    return hub


def test_setting_is_off_by_default_and_can_be_turned_on_by_env(monkeypatch) -> None:
    monkeypatch.delenv("QLL_THETADATA_RECONCILE_ENABLED", raising=False)
    assert Settings(_env_file=None).thetadata_reconcile_enabled is False
    monkeypatch.setenv("QLL_THETADATA_RECONCILE_ENABLED", "true")
    assert Settings(_env_file=None).thetadata_reconcile_enabled is True


def test_the_hub_keeps_reconcile_on_when_nobody_says_otherwise() -> None:
    """Library default stays True: only the Worker's configuration (Settings, default False) turns it off."""
    assert _hub(enabled=None, calls=[])._reconcile_enabled is True


@pytest.mark.asyncio
async def test_disabled_hub_never_creates_the_task_nor_calls_rest(monkeypatch, caplog) -> None:
    monkeypatch.setattr(provider_module, "RECONCILE_INTERVAL_SECONDS", 0.01)
    calls: list[str] = []
    hub = _hub(enabled=False, calls=calls)
    with caplog.at_level("INFO"):
        hub.start()
    assert hub._reconcile_task is None
    assert hub._task is not None and hub._watchdog_task is not None and hub._message_processor_task is not None   # the stream itself still starts
    assert any("periodic reconcile() is disabled" in r.message for r in caplog.records)
    await asyncio.sleep(0.2)                       # many intervals of 0.01 s would have elapsed
    assert not [c for c in calls if c.endswith("/history/ohlc")], "no per-contract REST volume check may run"
    await hub.stop()
    assert hub._reconcile_task is None


@pytest.mark.asyncio
async def test_enabled_hub_still_reconciles_this_is_the_rollback_path(monkeypatch) -> None:
    monkeypatch.setattr(provider_module, "RECONCILE_INTERVAL_SECONDS", 0.01)
    calls: list[str] = []
    hub = _hub(enabled=True, calls=calls)
    hub.start()
    assert hub._reconcile_task is not None
    for _ in range(100):
        if any(c.endswith("/history/ohlc") for c in calls):
            break
        await asyncio.sleep(0.02)
    await hub.stop()
    assert any(c.endswith("/history/ohlc") for c in calls), "with the switch on, reconcile() must run exactly as before"
    assert hub._reconcile_task is None


@pytest.mark.parametrize("env_value, expected", [(None, False), ("true", True)])
def test_container_passes_the_setting_to_the_provider(monkeypatch, env_value, expected) -> None:
    if env_value is None:
        monkeypatch.delenv("QLL_THETADATA_RECONCILE_ENABLED", raising=False)
    else:
        monkeypatch.setenv("QLL_THETADATA_RECONCILE_ENABLED", env_value)
    settings = Settings(_env_file=None, DATABASE_URL="sqlite+aiosqlite:///:memory:", enable_scheduler=False, data_provider="thetadata")
    monkeypatch.setattr(container_module, "get_settings", lambda: settings)
    container = container_module.build_container()
    assert isinstance(container.market_data_provider, ThetaDataProvider)
    assert container.market_data_provider._hub._reconcile_enabled is expected
