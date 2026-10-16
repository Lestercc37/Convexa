"""Volume corrections kept apart from the live counter (migration 0044): the pure merge, the expiration choice, the in-memory storage, the pass and the /chain reply."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from backend import volume_correction_pass
from backend.adapters.storage.memory import InMemoryStorage
from backend.domain.entities import ContractType, OptionChain, OptionContract, OptionGreeks
from backend.domain.use_cases.volume_corrections import apply_volume_corrections, expirations_to_correct
from backend.main import app

TODAY = date(2026, 10, 16)
NOW = datetime(2026, 10, 16, 14, 30, tzinfo=UTC)


def _contract(strike: int, volume: int, expiration: date = TODAY, right: ContractType = ContractType.PUT) -> OptionContract:
    letter = "P" if right == ContractType.PUT else "C"
    return OptionContract(
        underlying="SPX",
        strike=Decimal(strike),
        expiration=expiration,
        contract_type=right,
        occ_symbol=f"SPXW{expiration:%y%m%d}{letter}{strike * 1000:08d}",
        bid=Decimal(1),
        ask=Decimal(2),
        last=Decimal(1),
        volume=volume,
        open_interest=10,
        iv=Decimal("0.2"),
        greeks=OptionGreeks(*(Decimal(0) for _ in range(6))),
    )


def _chain(*contracts: OptionContract) -> OptionChain:
    return OptionChain(symbol="SPX", as_of=NOW, spot_price=Decimal(7660), contracts=tuple(contracts))


def test_a_correction_raises_the_volume_only_when_it_is_higher() -> None:
    late, level, ahead = _contract(7550, 1416), _contract(7600, 500), _contract(7650, 900)
    corrected = apply_volume_corrections(_chain(late, level, ahead), {late.occ_symbol: 7229, level.occ_symbol: 500, ahead.occ_symbol: 100})
    assert [contract.volume for contract in corrected.contracts] == [7229, 500, 900]
    assert corrected.contracts[0].strike == late.strike and len(corrected.contracts) == 3  # nothing added, nothing removed


def test_without_corrections_the_chain_is_returned_untouched() -> None:
    chain = _chain(_contract(7550, 1416))
    assert apply_volume_corrections(chain, {}) is chain
    assert apply_volume_corrections(chain, {"SPXW261016P07999000": 5}) is chain


def test_expirations_inside_the_tactical_window_or_the_nearest_one() -> None:
    listed = [date(2026, 10, 15), TODAY, date(2026, 10, 17), date(2026, 10, 18), date(2026, 10, 23), TODAY]
    assert expirations_to_correct(listed, TODAY) == [TODAY, date(2026, 10, 17), date(2026, 10, 18)]
    assert expirations_to_correct([date(2026, 10, 21), date(2026, 10, 28)], TODAY) == [date(2026, 10, 21)]  # VIX on a Friday: next Wednesday
    assert expirations_to_correct([date(2026, 10, 15)], TODAY) == []  # everything expired


def test_the_in_memory_correction_only_grows_within_the_trade_date() -> None:
    storage = InMemoryStorage()
    storage.save_volume_corrections({"A": 100, "B": 50})
    storage.save_volume_corrections({"A": 80, "B": 70})
    assert storage.get_volume_corrections(["A", "B", "C"]) == {"A": 100, "B": 70}
    assert storage.get_volume_corrections([]) == {}


class _FakeProvider:
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, date]] = []
        self._fail_on = fail_on

    def get_option_volume_snapshot(self, symbol: str, expiration: date) -> dict[str, int]:
        self.calls.append((symbol, expiration))
        if symbol == self._fail_on:
            raise RuntimeError("ThetaData request failed: 503")
        return {f"{symbol}{expiration:%y%m%d}P00007550": 1000 + len(self.calls)}


def _storage_with_chains(*symbols: str) -> InMemoryStorage:
    storage = InMemoryStorage()
    for symbol in symbols:
        contracts = (_contract(7550, 10), _contract(7600, 10, expiration=date(2026, 10, 17)))
        storage.save_chain_snapshot(OptionChain(symbol=symbol, as_of=NOW, spot_price=Decimal(7660), contracts=contracts))
    return storage


def test_the_pass_asks_each_symbol_once_per_expiration_and_stores_what_it_reads() -> None:
    storage, provider = _storage_with_chains("SPX", "SPY"), _FakeProvider()
    summary = volume_correction_pass.run_pass(storage, provider, NOW, sleep=lambda _s: None)
    assert provider.calls == [("SPY", TODAY), ("SPY", date(2026, 10, 17)), ("SPX", TODAY), ("SPX", date(2026, 10, 17))]  # the order of ACTIVE_UNDERLYINGS
    assert summary["requests"] == 4 and summary["contracts"] == 4 and summary["stopped_because"] is None
    assert len(storage.get_volume_corrections([f"{s}{d:%y%m%d}P00007550" for s in ("SPX", "SPY") for d in (TODAY, date(2026, 10, 17))])) == 4


def test_the_first_failing_request_ends_the_pass_but_keeps_what_was_read() -> None:
    storage, provider = _storage_with_chains("SPX", "SPY"), _FakeProvider(fail_on="SPX")
    summary = volume_correction_pass.run_pass(storage, provider, NOW, sleep=lambda _s: None)
    assert summary["stopped_because"].startswith("SPX") and summary["requests"] == 2 and summary["contracts"] == 2
    assert len(provider.calls) == 3  # SPY x2 then the failing SPX call; nothing after it


def test_the_time_box_stops_the_pass() -> None:
    clock = iter([0.0, 1.0, 200.0, 200.0, 200.0, 200.0, 200.0])
    summary = volume_correction_pass.run_pass(_storage_with_chains("SPX"), _FakeProvider(), NOW, sleep=lambda _s: None, monotonic=lambda: next(clock))
    assert summary["stopped_because"] == "time box"


def test_the_pass_refuses_the_open_and_weekends() -> None:
    et = volume_correction_pass.EASTERN_TIME
    assert not volume_correction_pass.within_allowed_window(datetime(2026, 10, 16, 9, 40, tzinfo=et))
    assert not volume_correction_pass.within_allowed_window(datetime(2026, 10, 16, 10, 5, tzinfo=et))
    assert volume_correction_pass.within_allowed_window(datetime(2026, 10, 16, 10, 30, tzinfo=et))
    assert volume_correction_pass.within_allowed_window(datetime(2026, 10, 16, 16, 5, tzinfo=et))
    assert not volume_correction_pass.within_allowed_window(datetime(2026, 10, 16, 17, 0, tzinfo=et))
    assert not volume_correction_pass.within_allowed_window(datetime(2026, 10, 17, 11, 0, tzinfo=et))  # Saturday


def test_the_chain_reply_shows_the_corrected_total_and_the_stored_counter_stays_continuous() -> None:
    with TestClient(app) as client:
        storage = client.app.state.container.storage
        today = datetime.now(UTC).date()
        contracts = (_contract(7550, 1416, expiration=today), _contract(7600, 500, expiration=today))
        storage.save_chain_snapshot(OptionChain(symbol="SPX", as_of=datetime.now(UTC), spot_price=Decimal(7660), contracts=contracts))
        storage.save_volume_corrections({contracts[0].occ_symbol: 7229})

        reply = client.get("/api/v1/chain/SPX")
        stored = storage.get_latest_chain_snapshot("SPX")

    assert reply.status_code == 200
    volumes = {item["strike"]: item["volume"] for item in reply.json()["contracts"]}
    assert volumes[7550] == 7229 and volumes[7600] == 500
    assert [contract.volume for contract in stored.contracts] == [1416, 500]
