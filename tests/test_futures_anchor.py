"""The ES / NQ opening anchor saved automatically (Lester 2026-11-02 / 11-04): from NinjaTrader's 09:30 bar (POST /api/v1/feeds/futures-anchor)
or from the 09:41 Yahoo task, never over a number already saved that day and only when the offset check passes."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.adapters.storage.memory import InMemoryStorage
from backend.api.routes import futures_anchor_feed
from backend.domain.use_cases.futures_anchor import (
    anchor_from_bar,
    auto_save_anchor,
    check_anchor,
    contract_expiry,
    expected_offsets,
    normalize_future,
)
from backend.domain.use_cases.market_hours import EASTERN_TIME
from backend.main import app
from backend.scripts.es_nq_anchor_telegram import save_line

DAY = date(2026, 11, 5)
ES_DAYS = [(date(2026, 11, 4), 30.0)]
NQ_DAYS = [(date(2026, 11, 4), 220.0)]


def test_the_anchor_is_the_typical_price_of_the_bar_rounded_to_a_quarter() -> None:
    assert anchor_from_bar(Decimal("7612.50"), Decimal("7601.25"), Decimal("7608.00")) == Decimal("7607.25")
    assert anchor_from_bar(Decimal(28400), Decimal(28350), Decimal(28390)) == Decimal("28380.00")


def test_micro_contracts_and_ninjatrader_names_map_to_es_and_nq() -> None:
    assert [normalize_future(n) for n in ("ES", "MES", "ES 12-26", "MNQ 12-26", "NQ", "CL")] == ["ES", "ES", "ES", "NQ", "NQ", None]


def test_the_check_compares_with_the_last_days_and_falls_back_to_the_usual_range() -> None:
    days = [(date(2026, 11, 4), 30.0), (date(2026, 11, 3), 31.0), (date(2026, 11, 2), 29.0)]
    assert check_anchor("ES", Decimal(7640), 7610.0, days, DAY).ok
    failed = check_anchor("ES", Decimal(7660), 7610.0, days, DAY)
    assert not failed.ok and failed.offset == 50.0 and failed.usual == pytest.approx(30 / 44 * 43)  # 30 points with 44 days left -> 43 days left today
    assert check_anchor("NQ", Decimal(28400), 28180.0, [], DAY).ok  # +220, inside 190-250
    assert not check_anchor("ES", Decimal(7640), None, [], DAY).ok  # no index reading: nothing to compare with


def test_an_automatic_save_never_overwrites_and_says_who_saved_first() -> None:
    storage = InMemoryStorage()
    first = auto_save_anchor(storage, "ES", DAY, Decimal(7640), "ninjatrader", 7610.0, ES_DAYS)
    second = auto_save_anchor(storage, "ES", DAY, Decimal("7640.50"), "yahoo", 7610.0, ES_DAYS)
    assert first.status == "saved"
    assert second.status == "exists" and second.existing == Decimal(7640) and second.existing_source == "ninjatrader"
    assert storage.get_future_price_anchor("ES", DAY) == Decimal(7640)

    storage.set_future_price_anchor("NQ", DAY, Decimal(28400))  # typed by Lester
    typed = auto_save_anchor(storage, "NQ", DAY, Decimal(28410), "yahoo", 28180.0, NQ_DAYS)
    assert typed.status == "exists" and typed.existing_source == "manual"
    assert storage.get_future_price_anchor("NQ", DAY) == Decimal(28400)


def test_a_failed_check_or_a_dry_run_writes_nothing() -> None:
    storage = InMemoryStorage()
    assert auto_save_anchor(storage, "ES", DAY, Decimal(7700), "yahoo", 7610.0, ES_DAYS).status == "check_failed"
    assert auto_save_anchor(storage, "ES", DAY, Decimal(7640), "yahoo", 7610.0, ES_DAYS, dry_run=True).status == "would_save"
    assert storage.get_future_price_anchor("ES", DAY) is None


def test_the_telegram_line_says_who_saved_it() -> None:
    storage = InMemoryStorage()
    auto_save_anchor(storage, "ES", DAY, Decimal(7640), "ninjatrader", 7610.0, ES_DAYS)
    yahoo = auto_save_anchor(storage, "ES", DAY, Decimal("7640.75"), "yahoo", 7610.0, ES_DAYS)
    assert save_line("ES", yahoo) == "ES: ya guardado por NinjaTrader 09:31: 7,640.00 (Yahoo da 7,640.75, diferencia +0.75)"
    saved = auto_save_anchor(storage, "NQ", DAY, Decimal(28400), "yahoo", 28180.0, NQ_DAYS)
    assert save_line("NQ", saved) == "NQ: guardado ✓ (Yahoo)"


@pytest.fixture
def feed(tmp_path, monkeypatch):
    key_file = tmp_path / "key.txt"
    key_file.write_text("test-key-not-real", encoding="ascii")
    monkeypatch.setattr(futures_anchor_feed, "get_settings", lambda: SimpleNamespace(ninjatrader_anchor_key_file=str(key_file)))
    monkeypatch.setattr(futures_anchor_feed, "_now_et", lambda: datetime(2026, 11, 5, 9, 31, 2, tzinfo=EASTERN_TIME))
    monkeypatch.setattr(futures_anchor_feed.futures_anchor_queries, "first_minute_stats", lambda storage, symbol, day_open: (7610.0, 4.0))
    monkeypatch.setattr(futures_anchor_feed.futures_anchor_queries, "recent_offsets", lambda storage, future, index, before: ES_DAYS)
    with TestClient(app) as client:
        yield client


BAR = {"symbol": "MES 12-26", "bar_start": "2026-11-05T09:30:00-05:00", "high": 7642.5, "low": 7636.0, "close": 7641.0}


def test_the_feed_needs_the_key(feed) -> None:
    assert feed.post("/api/v1/feeds/futures-anchor", json=BAR).status_code == 401
    assert feed.post("/api/v1/feeds/futures-anchor", json=BAR, headers={"X-Convexa-Key": "wrong"}).status_code == 401


def test_the_feed_saves_todays_0930_bar_once_and_dry_run_writes_nothing(feed) -> None:
    headers = {"X-Convexa-Key": "test-key-not-real"}
    dry = feed.post("/api/v1/feeds/futures-anchor", json={**BAR, "dry_run": True}, headers=headers)
    assert dry.status_code == 200 and dry.json()["status"] == "would_save" and dry.json()["future"] == "ES"
    assert app.state.container.storage.get_future_price_anchor("ES", DAY) is None

    saved = feed.post("/api/v1/feeds/futures-anchor", json=BAR, headers=headers)
    assert saved.json()["status"] == "saved" and Decimal(str(saved.json()["anchor"])) == Decimal("7639.75")
    again = feed.post("/api/v1/feeds/futures-anchor", json={**BAR, "high": 7650}, headers=headers)
    assert again.json()["status"] == "exists" and again.json()["existing_source"] == "ninjatrader"


def test_the_feed_refuses_any_other_bar(feed) -> None:
    headers = {"X-Convexa-Key": "test-key-not-real"}
    late = feed.post("/api/v1/feeds/futures-anchor", json={**BAR, "bar_start": "2026-11-05T09:31:00-05:00"}, headers=headers)
    yesterday = feed.post("/api/v1/feeds/futures-anchor", json={**BAR, "bar_start": "2026-11-04T09:30:00-05:00"}, headers=headers)
    oil = feed.post("/api/v1/feeds/futures-anchor", json={**BAR, "symbol": "CL 12-26"}, headers=headers)
    assert (late.status_code, yesterday.status_code, oil.status_code) == (422, 422, 422)


# 2026-11-05: NinjaTrader's 09:31 values were right (Yahoo 09:41 within 0.25 / 0.5) but the median of the last 5 offsets rejected them.
ES_BEFORE_1105 = [(date(2026, 11, 4), 32.87), (date(2026, 11, 3), 31.83), (date(2026, 11, 2), 33.94), (date(2026, 10, 30), 34.32), (date(2026, 10, 29), 40.90)]
NQ_BEFORE_1105 = [(date(2026, 11, 4), 139.22), (date(2026, 11, 3), 150.16), (date(2026, 11, 2), 152.42), (date(2026, 10, 30), 151.06), (date(2026, 10, 29), 179.85)]


def test_the_good_ninjatrader_values_of_2026_11_05_now_pass() -> None:
    es = check_anchor("ES", Decimal("7702.75"), 7676.02, ES_BEFORE_1105, DAY, first_minute_range=12.93)
    nq = check_anchor("NQ", Decimal("28159.25"), 28048.29, NQ_BEFORE_1105, DAY, first_minute_range=77.09)
    assert es.ok and nq.ok
    assert es.usual == pytest.approx(31.73, abs=0.01)  # 33.94 / 44 days x 43 (the median basis per day left)


def test_a_wrong_bar_or_a_typo_is_still_rejected() -> None:
    # One minute late on a quiet open (index range 6): about 10-20 ES points off; a slipped digit: hundreds.
    assert not check_anchor("ES", Decimal("7722.75"), 7676.02, ES_BEFORE_1105, DAY, first_minute_range=6.0).ok
    assert not check_anchor("NQ", Decimal("28259.25"), 28048.29, NQ_BEFORE_1105, DAY, first_minute_range=30.0).ok
    assert not check_anchor("ES", Decimal("7072.75"), 7676.02, ES_BEFORE_1105, DAY, first_minute_range=12.93).ok


def test_the_contract_rolls_8_days_before_its_third_friday_and_both_are_accepted_around_it() -> None:
    assert contract_expiry(DAY) == date(2026, 12, 18)
    assert contract_expiry(date(2026, 12, 9)) == date(2026, 12, 18)
    assert contract_expiry(date(2026, 12, 10)) == date(2027, 3, 19)
    history = [(date(2026, 12, 4), 0.62 * 14)]  # 0.62 points per day left
    assert len(expected_offsets(history, date(2026, 11, 20))) == 1
    before, after = expected_offsets(history, date(2026, 12, 8)), expected_offsets(history, date(2026, 12, 11))
    assert before == pytest.approx([0.62 * 10, 0.62 * 101])  # still Dec, or already Mar
    assert after == pytest.approx([0.62 * 98, 0.62 * 7])  # Mar now, Dec a few days late
