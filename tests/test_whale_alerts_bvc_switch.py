"""BVC whale alerts (WhaleAlertsEngine.process(), fed by the REST scheduler) can be switched off
while the live trade stream (process_trade(), Lee-Ready) runs -- see Settings.whale_alerts_bvc_active."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backend.adapters.providers.mock import MockDataProvider
from backend.adapters.storage.memory import InMemoryStorage
from backend.adapters.storage.postgresql import LEE_READY_ONLY_SQL, PostgreSQLStorage
from backend.adapters.storage.postgresql_async import AsyncPostgreSQLStorage
from backend.core.container import build_whale_alerts_engine
from backend.core.settings import Settings
from backend.domain.entities import FlowEvent, FlowEventType, LatestQuote, OptionChain, Side
from backend.domain.use_cases import WhaleAlertsEngine, WhaleAlertType


class _CountingStorage(InMemoryStorage):
    def __init__(self) -> None:
        super().__init__()
        self.saved = 0

    def save_whale_alert(self, alert) -> None:  # type: ignore[no-untyped-def]
        self.saved += 1
        super().save_whale_alert(alert)


def _chain(base: OptionChain, volume: int, period: int) -> OptionChain:
    contract = replace(base.contracts[0], volume=volume, last=Decimal("1.00"))
    return replace(
        base,
        as_of=datetime(2026, 1, 15, 14, 30, tzinfo=UTC) + timedelta(minutes=period),
        contracts=(contract,),
    )


def _run_bvc_scenario(engine: WhaleAlertsEngine) -> list:
    """Same sequence as test_engine_emits_unusual_after_five_previous_periods:
    five quiet periods, a $45,000 one, then a reading that closes its bucket."""
    base = MockDataProvider().get_option_chain("IWM")
    cumulative = 100
    outputs = [engine.process(_chain(base, cumulative, 0))]
    for period in range(1, 6):
        cumulative += 100
        outputs.append(engine.process(_chain(base, cumulative, period)))
    cumulative += 450
    outputs.append(engine.process(_chain(base, cumulative, 6)))
    outputs.append(engine.process(_chain(base, cumulative, 7)))
    return outputs


# --- (a) flag off: process() is a no-op

def test_process_with_bvc_disabled_returns_empty_and_never_saves_or_records_anything() -> None:
    storage = _CountingStorage()
    engine = WhaleAlertsEngine(storage, bvc_alerts_enabled=False)

    outputs = _run_bvc_scenario(engine)

    assert all(output == () for output in outputs)
    assert storage.saved == 0
    assert storage.get_recent_whale_alerts("IWM") == []
    assert engine.recent_alerts("IWM") == ()
    # First instruction of process() is the guard: no per-contract state was even created.
    assert engine._states == {}


# --- (b) flag on (default and explicit): identical to the behavior before this change

def test_process_with_bvc_enabled_explicitly_matches_the_default_engine() -> None:
    default_storage, explicit_storage = _CountingStorage(), _CountingStorage()
    default_outputs = _run_bvc_scenario(WhaleAlertsEngine(default_storage))
    explicit_outputs = _run_bvc_scenario(WhaleAlertsEngine(explicit_storage, bvc_alerts_enabled=True))

    assert default_outputs == explicit_outputs
    assert default_storage.saved == explicit_storage.saved == 1
    alert = default_outputs[-1][0]
    assert alert.alert_type is WhaleAlertType.UNUSUAL
    assert alert.amount == Decimal("45000.00")


# --- (c) process_trade() is unaffected by the flag

TRADE_OCC_SYMBOL = "IWM260220C00185000"
TRADE_BASE_TIME = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
BUY_QUOTE = LatestQuote(bid=Decimal("0.01"), ask=Decimal("0.02"), as_of=TRADE_BASE_TIME)


def _trade(period: int, premium: str) -> FlowEvent:
    return FlowEvent(
        symbol="IWM",
        occ_symbol=TRADE_OCC_SYMBOL,
        as_of=TRADE_BASE_TIME + timedelta(minutes=period),
        event_type=FlowEventType.UNUSUAL,
        premium=Decimal(premium),
        size=1,
        aggressor_side=Side.UNKNOWN,
    )


@pytest.mark.parametrize("bvc_enabled", [True, False])
def test_process_trade_behaves_identically_whatever_the_bvc_flag(bvc_enabled: bool) -> None:
    storage = _CountingStorage()
    engine = WhaleAlertsEngine(storage, bvc_alerts_enabled=bvc_enabled)

    for period in range(6):
        assert engine.process_trade(_trade(period, "100"), BUY_QUOTE) == ()
    engine.process_trade(_trade(6, "45000"), BUY_QUOTE)
    alerts = engine.process_trade(_trade(7, "100"), BUY_QUOTE)

    assert len(alerts) == 1
    assert alerts[0].alert_type is WhaleAlertType.UNUSUAL
    assert alerts[0].amount == Decimal(45000)
    assert alerts[0].estimated_buy_volume == Decimal(45000)
    assert alerts[0].estimated_sell_volume == Decimal(0)
    assert alerts[0].quote_unavailable is False
    assert storage.saved == 1
    # The Lee-Ready alert's buy+sell equals its amount: the property the read filter relies on.
    assert alerts[0].estimated_buy_volume + alerts[0].estimated_sell_volume == alerts[0].amount
    flow = engine.symbol_flow("IWM")
    assert flow is not None and flow.net_call_premium == Decimal(45000 + 6 * 100 + 100)


def test_disabled_process_does_not_disturb_process_trade_on_the_same_contract() -> None:
    engine = WhaleAlertsEngine(InMemoryStorage(), bvc_alerts_enabled=False)
    base = MockDataProvider().get_option_chain("IWM")
    shared_occ = base.contracts[0].occ_symbol
    engine.process(_chain(base, 100, 0))
    for period in range(6):
        engine.process_trade(
            FlowEvent("IWM", shared_occ, TRADE_BASE_TIME + timedelta(minutes=period),
                      FlowEventType.UNUSUAL, Decimal(100), 1, Side.UNKNOWN),
            BUY_QUOTE,
        )
    engine.process_trade(
        FlowEvent("IWM", shared_occ, TRADE_BASE_TIME + timedelta(minutes=6),
                  FlowEventType.UNUSUAL, Decimal(45000), 1, Side.UNKNOWN),
        BUY_QUOTE,
    )
    alerts = engine.process_trade(
        FlowEvent("IWM", shared_occ, TRADE_BASE_TIME + timedelta(minutes=7),
                  FlowEventType.UNUSUAL, Decimal(100), 1, Side.UNKNOWN),
        BUY_QUOTE,
    )
    assert len(alerts) == 1 and alerts[0].amount == Decimal(45000)


# --- (d) flag selection by provider, and by configuration

def _settings(monkeypatch: pytest.MonkeyPatch, **values: object) -> Settings:
    monkeypatch.delenv("QLL_WHALE_ALERTS_BVC_ENABLED", raising=False)
    monkeypatch.delenv("QLL_DATA_PROVIDER", raising=False)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_bvc_alerts_default_off_with_thetadata_and_on_with_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings(monkeypatch, data_provider="thetadata").whale_alerts_bvc_active is False
    assert _settings(monkeypatch, data_provider="mock").whale_alerts_bvc_active is True
    assert _settings(monkeypatch).whale_alerts_bvc_active is True  # default provider is mock


def test_bvc_alerts_can_be_forced_either_way_by_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings(monkeypatch, data_provider="thetadata", whale_alerts_bvc_enabled=True).whale_alerts_bvc_active is True
    assert _settings(monkeypatch, data_provider="mock", whale_alerts_bvc_enabled=False).whale_alerts_bvc_active is False

    monkeypatch.setenv("QLL_WHALE_ALERTS_BVC_ENABLED", "true")
    assert Settings(_env_file=None, data_provider="thetadata").whale_alerts_bvc_active is True  # type: ignore[call-arg]
    monkeypatch.setenv("QLL_WHALE_ALERTS_BVC_ENABLED", "false")
    assert Settings(_env_file=None, data_provider="mock").whale_alerts_bvc_active is False  # type: ignore[call-arg]


def test_build_whale_alerts_engine_applies_the_flag_and_defaults_to_enabled() -> None:
    assert build_whale_alerts_engine(InMemoryStorage())._bvc_alerts_enabled is True
    assert build_whale_alerts_engine(InMemoryStorage(), bvc_alerts_enabled=False)._bvc_alerts_enabled is False


def test_build_container_wires_the_flag_from_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.core import container as container_module

    for provider, expected in (("mock", True), ("thetadata", False)):
        settings = _settings(monkeypatch, data_provider=provider)
        monkeypatch.setattr(container_module, "get_settings", lambda s=settings: s)
        built = container_module.build_container()
        assert built.whale_alerts_engine._bvc_alerts_enabled is expected


# --- read filter: SQL text only here (the real-table behavior is in test_postgresql_integration.py)

class _NoRows(list):
    def all(self) -> list:
        return []


class _RecordingResult:
    def mappings(self) -> _NoRows:
        return _NoRows()


class _RecordingSession:
    def __init__(self, statements: list[str]) -> None:
        self._statements = statements

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, statement, params=None):  # type: ignore[no-untyped-def]
        self._statements.append(str(statement))
        return _RecordingResult()


@pytest.mark.parametrize("lee_ready_only", [True, False])
def test_sync_storage_adds_the_source_predicate_only_when_asked(lee_ready_only: bool) -> None:
    statements: list[str] = []
    storage = PostgreSQLStorage(lambda: _RecordingSession(statements), whale_alerts_lee_ready_only=lee_ready_only)  # type: ignore[arg-type]
    storage.get_recent_whale_alerts("SPX")
    assert ("abs((w.estimated_buy_volume + w.estimated_sell_volume) - w.amount) < 1" in statements[0]) is lee_ready_only
    assert LEE_READY_ONLY_SQL.startswith("AND abs(")


@pytest.mark.asyncio
@pytest.mark.parametrize("lee_ready_only", [True, False])
async def test_async_storage_adds_the_source_predicate_only_when_asked(lee_ready_only: bool) -> None:
    statements: list[str] = []

    class _AsyncSession(_RecordingSession):
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def execute(self, statement, params=None):  # type: ignore[no-untyped-def]
            self._statements.append(str(statement))
            return _RecordingResult()

    storage = AsyncPostgreSQLStorage(lambda: _AsyncSession(statements), whale_alerts_lee_ready_only=lee_ready_only)  # type: ignore[arg-type]
    await storage.get_recent_whale_alerts("SPX")
    assert ("abs((w.estimated_buy_volume + w.estimated_sell_volume) - w.amount) < 1" in statements[0]) is lee_ready_only
