"""EMA cross alerts worker (migration 0051; run as its own service: python -m backend.ema_alerts_worker).

Two loops:
- evaluation: a few seconds after every minute boundary of the regular session, for each (symbol, timeframe) that has enabled rules and whose candle just closed, reads the
  stored candles (warm-up sessions included, ES / NQ through SPX / NDX), evaluates every rule (backend/domain/use_cases/ema_alerts.py), stores its side, and sends a
  Telegram message when a rule's EMAs crossed in its direction;
- linking: long-polls the bot for messages; a valid one-time code (from the dashboard / app) links that chat to its Convexa user.

Never raises out of a loop: a failed read / send is logged (without the token: see backend/adapters/telegram.py) and the next minute tries again. Without a bot token
(QLL_TELEGRAM_BOT_TOKEN) the rules are still evaluated and their side stored, nothing is sent.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from backend.adapters.storage.ema_alerts_repo import ema_alert_repository
from backend.adapters.telegram import TelegramClient, TelegramError
from backend.core.container import build_container
from backend.domain.use_cases.ema_alerts import (
    CANDLE_SOURCE_SYMBOL,
    SEED_SESSIONS,
    TIMEFRAME_SECONDS,
    alert_text,
    closed_candles,
    code_from_message,
    evaluate,
)
from backend.domain.use_cases.market_hours import EASTERN_TIME

logger = logging.getLogger("backend.ema_alerts_worker")
EVALUATE_DELAY_SECONDS = 5  # after the minute boundary, so the last price of the candle is stored
CANDLE_LOOKBACK = timedelta(days=21)  # bounds the candle scan; 10 sessions fit easily


def _due(timeframe: str, minute_start: datetime) -> bool:
    """True when a candle of `timeframe` ended exactly at `minute_start` (buckets counted from 09:30 ET, like the chart)."""
    local = minute_start.astimezone(EASTERN_TIME)
    since_open = (local.hour * 60 + local.minute) - (9 * 60 + 30)
    return (
        since_open > 0
        and since_open * 60 % TIMEFRAME_SECONDS[timeframe] == 0
        or (local.hour, local.minute) == (16, 0)
    )


async def evaluate_once(container, repo, telegram: TelegramClient | None, now: datetime) -> int:
    rules = await asyncio.to_thread(repo.rules)
    minute_start = now.replace(second=0, microsecond=0)
    groups = defaultdict(list)
    for rule in rules:
        if _due(rule.timeframe, minute_start):
            groups[(CANDLE_SOURCE_SYMBOL.get(rule.symbol, rule.symbol), rule.timeframe)].append(
                rule
            )
    sent = 0
    for (source, timeframe), group in groups.items():
        try:
            candles = await container.async_market_storage.get_price_candles(
                source,
                TIMEFRAME_SECONDS[timeframe],
                SEED_SESSIONS[timeframe] + 1,
                now - CANDLE_LOOKBACK,
            )
        except Exception:
            logger.exception("EMA alerts: reading the %s %s candles failed", source, timeframe)
            continue
        closed = closed_candles(
            [(candle.time, float(candle.close)) for candle in candles], timeframe, now
        )
        for rule in group:
            result = evaluate(rule, closed)
            if result is None:
                continue
            await asyncio.to_thread(
                repo.save_state, rule.id, result.state, result.candle, result.fired is not None
            )
            if result.fired is None:
                continue
            chat_id = await asyncio.to_thread(repo.chat_of, rule.user_id)
            if chat_id is None or telegram is None:
                logger.info(
                    "EMA alerts: rule %d crossed %s but has no linked chat / bot",
                    rule.id,
                    result.fired,
                )
                continue
            try:
                await telegram.send_message(chat_id, alert_text(rule, result))
                sent += 1
            except TelegramError as exc:
                logger.warning("EMA alerts: rule %d: %s", rule.id, exc)
    return sent


async def _evaluate_forever(container, repo, telegram) -> None:
    while True:
        now = datetime.now(UTC)
        next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        await asyncio.sleep((next_minute - now).total_seconds() + EVALUATE_DELAY_SECONDS)
        local = datetime.now(EASTERN_TIME)
        if local.weekday() >= 5 or not ((9, 31) <= (local.hour, local.minute) <= (16, 1)):
            continue
        try:
            await evaluate_once(container, repo, telegram, datetime.now(UTC))
        except Exception:
            logger.exception("EMA alerts: evaluation pass failed")


async def handle_update(repo, telegram: TelegramClient, update: dict, now: datetime) -> None:
    message = update.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    text = message.get("text") or ""
    if chat_id is None or not text:
        return
    code = code_from_message(text)
    if code is None:
        await telegram.send_message(
            chat_id,
            "Hola. Para recibir las alertas de Convexa, genera tu código en Convexa (Alertas EMA → Vincular Telegram) y envíamelo aquí.",
        )
        return
    user_id = await asyncio.to_thread(repo.link, code, int(chat_id), now)
    if user_id is None:
        await telegram.send_message(
            chat_id,
            "Ese código no es válido o ya venció (dura 10 minutos). Genera uno nuevo en Convexa.",
        )
    else:
        await telegram.send_message(
            chat_id, "Listo: este chat recibirá tus alertas de cruce de EMA de Convexa."
        )


async def _link_forever(repo, telegram: TelegramClient) -> None:
    offset = None
    while True:
        try:
            updates = await telegram.get_updates(offset)
        except TelegramError as exc:
            logger.warning("EMA alerts: %s", exc)
            await asyncio.sleep(10)
            continue
        for update in updates:
            offset = int(update.get("update_id", 0)) + 1
            try:
                await handle_update(repo, telegram, update, datetime.now(UTC))
            except TelegramError as exc:
                logger.warning("EMA alerts: %s", exc)
            except Exception:
                logger.exception("EMA alerts: handling a Telegram message failed")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx logs every request URL at INFO, and the URL carries the bot token: keep it at WARNING.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    container = build_container()
    repo = ema_alert_repository(container)
    token = container.settings.telegram_bot_token
    telegram = (
        TelegramClient(token.get_secret_value())
        if token is not None and token.get_secret_value()
        else None
    )
    if telegram is None:
        logger.warning(
            "EMA alerts: QLL_TELEGRAM_BOT_TOKEN is not set: rules are evaluated, nothing is sent"
        )
    tasks = [asyncio.create_task(_evaluate_forever(container, repo, telegram))]
    if telegram is not None:
        tasks.append(asyncio.create_task(_link_forever(repo, telegram)))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
