"""EMA cross alerts by Telegram (migration 0051): evaluation, linking, the bot client and the routes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from backend import ema_alerts_worker as worker
from backend.adapters.storage.ema_alerts_repo import InMemoryEmaAlertRepository
from backend.adapters.telegram import TelegramClient, TelegramError
from backend.domain.entities import PriceCandle
from backend.domain.use_cases.ema_alerts import (
    EmaRule,
    closed_candles,
    code_from_message,
    ema_series,
    evaluate,
)

T0 = datetime(2026, 10, 19, 13, 30, tzinfo=UTC)  # 09:30 ET


def _candles(closes: list[float], step_minutes: int = 5) -> list[tuple[datetime, float]]:
    return [(T0 + timedelta(minutes=step_minutes * i), close) for i, close in enumerate(closes)]


def _rule(**changes) -> EmaRule:
    base = dict(
        id=1,
        user_id=7,
        symbol="NQ",
        timeframe="5m",
        fast_period=3,
        slow_period=5,
        direction="both",
        last_state=None,
        last_candle=None,
    )
    base.update(changes)
    return EmaRule(**base)


def test_the_ema_matches_the_chart_formula() -> None:
    # frontend/lib/ema.ts: seeded with the first close, alpha = 2 / (period + 1)
    assert ema_series([10, 11, 12], 3) == pytest.approx([10, 10.5, 11.25])


def test_a_cross_fires_once_in_its_direction_and_the_first_evaluation_only_stores_the_side() -> (
    None
):
    falling = [100 - i for i in range(10)]
    first = evaluate(_rule(), _candles(falling))
    assert (
        first is not None and first.state == -1 and first.fired is None
    )  # no alert for a side that existed before the rule
    rising = falling + [95, 100, 106, 112]
    crossed = evaluate(_rule(last_state=-1, last_candle=first.candle), _candles(rising))
    assert crossed is not None and crossed.state == 1 and crossed.fired == "up"
    again = evaluate(_rule(last_state=1, last_candle=crossed.candle), _candles(rising))
    assert again is None  # no new closed candle
    only_down = evaluate(
        _rule(last_state=-1, last_candle=first.candle, direction="down"), _candles(rising)
    )
    assert only_down is not None and only_down.fired is None and only_down.state == 1


def test_not_enough_history_for_the_slow_ema_gives_nothing() -> None:
    assert evaluate(_rule(), _candles([1, 2, 3])) is None


def test_only_closed_candles_count_and_the_last_hour_closes_at_four() -> None:
    candles = _candles([1.0, 2.0, 3.0])  # 09:30, 09:35, 09:40
    assert len(closed_candles(candles, "5m", T0 + timedelta(minutes=14))) == 2
    last_hour = [(datetime(2026, 10, 19, 19, 30, tzinfo=UTC), 5.0)]  # 15:30 ET
    assert (
        closed_candles(last_hour, "1h", datetime(2026, 10, 19, 20, 0, 5, tzinfo=UTC)) == last_hour
    )


def test_the_link_code_is_read_from_start_or_plain_messages() -> None:
    assert code_from_message("/start ABC234") == "ABC234"
    assert code_from_message("abc-234") == "ABC234"
    assert code_from_message("hola") is None


def test_the_bot_client_never_puts_the_token_in_its_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    client = TelegramClient(
        "123456:SECRET-TOKEN", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(TelegramError) as caught:
        asyncio.run(client.send_message(1, "x"))
    assert "SECRET" not in str(caught.value) and caught.value.__cause__ is None


class _Bot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


def test_a_valid_code_links_the_chat_and_a_bad_one_is_refused() -> None:
    repo, bot, now = InMemoryEmaAlertRepository(), _Bot(), datetime(2026, 10, 19, tzinfo=UTC)
    repo.new_code(7, "ABC234", now)
    asyncio.run(
        worker.handle_update(
            repo, bot, {"message": {"chat": {"id": 555}, "text": "/start ZZZ999"}}, now
        )
    )
    asyncio.run(
        worker.handle_update(
            repo, bot, {"message": {"chat": {"id": 555}, "text": "/start ABC234"}}, now
        )
    )
    assert repo.chat_of(7) == 555
    assert "no es válido" in bot.sent[0][1] and "Listo" in bot.sent[1][1]
    repo.new_code(7, "DEF234", now)
    assert repo.link("DEF234", 1, now + timedelta(minutes=11)) is None  # expired


def test_the_worker_sends_one_message_per_cross_to_the_linked_chat() -> None:
    repo, bot = InMemoryEmaAlertRepository(), _Bot()
    rule = repo.add_rule(7, "NQ", "5m", 3, 5, "both")
    repo.save_state(rule.id, -1, T0, fired=False)
    repo._chats[7] = 555
    closes = [100 - i for i in range(10)] + [95, 100, 106, 112]

    class _Storage:
        asked: list[str] = []

        async def get_price_candles(self, symbol, bucket_seconds, sessions, since):
            self.asked.append(symbol)
            return [
                PriceCandle(
                    time=t, open=Decimal(c), high=Decimal(c), low=Decimal(c), close=Decimal(c)
                )
                for t, c in _candles(closes)
            ]

    class _Container:
        async_market_storage = _Storage()

    now = T0 + timedelta(
        minutes=5 * len(closes), seconds=5
    )  # the last candle just closed, on a 5-minute boundary
    sent = asyncio.run(worker.evaluate_once(_Container(), repo, bot, now))
    assert sent == 1 and bot.sent[0][0] == 555 and "AL ALZA" in bot.sent[0][1]
    assert _Container.async_market_storage.asked == ["NDX"]  # NQ reads NDX candles
    assert (
        asyncio.run(worker.evaluate_once(_Container(), repo, bot, now)) == 0
    )  # the same candle never fires twice


def test_the_routes_manage_the_users_own_rules_and_link_code() -> None:
    # conftest's fake session (user 0) stands in for the login, as in every route test.
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as client:
        created = client.post("/api/v1/ema-alerts", json={"symbol": "nq", "timeframe": "5m"})
        wrong = client.post(
            "/api/v1/ema-alerts", json={"symbol": "NQ", "fast_period": 20, "slow_period": 8}
        )
        listed = client.get("/api/v1/ema-alerts").json()
        code = client.post("/api/v1/ema-alerts/telegram/code").json()
        deleted = client.delete(f"/api/v1/ema-alerts/{created.json()['id']}")
    assert (
        created.status_code == 201
        and created.json()["symbol"] == "NQ"
        and created.json()["fast_period"] == 8
    )
    assert wrong.status_code == 422
    assert listed["telegram_linked"] is False and len(listed["rules"]) >= 1
    assert (
        len(code["code"]) == 6
        and code["link"].endswith(code["code"])
        and code["expires_in_seconds"] == 600
    )
    assert deleted.status_code == 204
