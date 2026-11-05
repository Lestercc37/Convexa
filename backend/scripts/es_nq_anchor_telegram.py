"""Daily ES / NQ opening anchors to Lester's Telegram at 09:41 ET (Windows Task Scheduler on the server, weekdays; Lester 2026-10-30 "si").

Why on the server: the chat assistant's scheduled reminders only fire when it is idle, so the 09:41 message arrived 12 minutes late on 2026-10-30.

What it sends (same rule as the manual message since the 2026-10-16 offset fix): from Yahoo's 1-minute ES=F / NQ=F bars (about 10 minutes delayed, so the 09:30 bar exists
from ~09:41), ANCHOR = (high + low + close) / 3 of the 09:30 ET bar, rounded to 0.25 — the number Lester types as the opening price. Convexa's offset = anchor - median of
the first minute of stored SPX / NDX readings; it is checked against the last days; when the check passes and the day has no anchor yet the task also SAVES it
(source "yahoo"; Lester 2026-11-02), never over a number he typed or one NinjaTrader saved at 09:31 (backend/domain/use_cases/futures_anchor.py).

Skips weekends and days without SPX readings at 09:30 (market holidays). Retries the Yahoo bar every 30 s for up to 4 minutes. The bot token is read from the server's
.env (QLL_TELEGRAM_BOT_TOKEN, written by Lester) and never printed or logged; the chat id is in V:/Convexa/config/telegram_owner_chat.txt.
Usage: python -m backend.scripts.es_nq_anchor_telegram [--dry-run] [--test]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.adapters.storage.futures_anchor_queries import first_minute_stats, recent_offsets
from backend.core.container import build_container
from backend.domain.use_cases.futures_anchor import (
    AutoSaveResult,
    anchor_from_bar,
    auto_save_anchor,
)
from backend.domain.use_cases.market_hours import EASTERN_TIME

logger = logging.getLogger("es_nq_anchor")
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d&includePrePost=true"
FUTURES = {
    "ES": ("ES=F", "SPX", (40, 60), 5),
    "NQ": ("NQ=F", "NDX", (190, 250), 15),
}  # yahoo, index, usual offset, warn margin
CHAT_FILE = Path("V:/Convexa/config/telegram_owner_chat.txt")
RETRY_SECONDS, RETRY_FOR = 30, timedelta(minutes=4)


class _TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QLL_", env_file=".env", extra="ignore")
    telegram_bot_token: SecretStr | None = None


def opening_bar(
    client: httpx.Client, yahoo_symbol: str, day: str
) -> tuple[float, float, float, float] | None:
    """(open, high, low, close) of the 09:30 ET bar of `day`, or None when Yahoo does not have it yet."""
    body = client.get(
        YAHOO.format(symbol=yahoo_symbol), headers={"User-Agent": "Mozilla/5.0"}, timeout=20
    ).json()
    result = body["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    for index, stamp in enumerate(result.get("timestamp") or []):
        if datetime.fromtimestamp(stamp, EASTERN_TIME).strftime("%Y-%m-%d %H:%M") == f"{day} 09:30":
            values = (
                quote["open"][index],
                quote["high"][index],
                quote["low"][index],
                quote["close"][index],
            )
            return None if any(v is None for v in values) else tuple(float(v) for v in values)
    return None


def save_line(name: str, result: AutoSaveResult | None) -> str | None:
    """What happened to the day's saved anchor (Lester 2026-11-02: the 09:41 task also SAVES it; never over a number he typed)."""
    if result is None:
        return None
    if result.status == "saved":
        return f"{name}: guardado ✓ (Yahoo)"
    if result.status == "would_save":
        return f"{name}: se guardaría (prueba, no se escribió nada)"
    if result.status == "exists":
        diff = result.anchor - result.existing if result.existing is not None else None
        tail = f" (Yahoo da {result.anchor:,.2f}, diferencia {diff:+.2f})" if diff is not None else ""
        if result.existing_source == "ninjatrader":
            return f"{name}: ya guardado por NinjaTrader 09:31: {result.existing:,.2f}{tail}"
        if result.existing_source == "manual":
            return f"{name}: ya lo escribiste tú: {result.existing:,.2f}{tail}"
        return f"{name}: ya estaba guardado ({result.existing_source}): {result.existing:,.2f}{tail}"
    return f"{name}: NO guardado. {result.check.warning} Revísalo y escríbelo tú."


def build_message(
    day: str, anchors: dict, offsets: dict, bars: dict, saves: dict, test: bool
) -> str:
    lines = [("PRUEBA · " if test else "") + f"Convexa · apertura ES / NQ · {day}"]
    for name in ("ES", "NQ"):
        if name in anchors:
            o, _h, _low, c = bars[name]
            off = offsets.get(name)
            lines.append(
                f"{name}: {anchors[name]:,.2f}   (vela 09:30 abrió {o:,.2f}, cerró {c:,.2f}"
                + (f"; {name} − {FUTURES[name][1]} = {off:+.1f})" if off is not None else ")")
            )
        else:
            lines.append(f"{name}: sin vela de 09:30 en Yahoo todavía")
    lines.extend(line for name in ("ES", "NQ") if (line := save_line(name, saves.get(name))) is not None)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print the message, send nothing")
    parser.add_argument("--test", action="store_true", help="mark the message as a test")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(
        logging.WARNING
    )  # its INFO lines carry request URLs, the Telegram one has the token

    now = datetime.now(EASTERN_TIME)
    day = now.strftime("%Y-%m-%d")
    if now.weekday() >= 5:
        logger.info("weekend, nothing to send")
        return 0
    day_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    container = build_container()
    stats = {name: first_minute_stats(container.storage, spec[1], day_open) for name, spec in FUTURES.items()}
    medians = {name: (value[0] if value else None) for name, value in stats.items()}
    if medians["ES"] is None and medians["NQ"] is None:
        logger.info("no SPX / NDX readings at 09:30 today: market closed, nothing to send")
        return 0

    bars: dict[str, tuple] = {}
    deadline = datetime.now(EASTERN_TIME) + RETRY_FOR
    with httpx.Client() as client:
        while True:
            for name, spec in FUTURES.items():
                if name not in bars:
                    try:
                        bar = opening_bar(client, spec[0], day)
                    except Exception as exc:  # network / format: retried
                        logger.warning("yahoo %s: %s", spec[0], type(exc).__name__)
                        bar = None
                    if bar is not None:
                        bars[name] = bar
            if len(bars) == len(FUTURES) or datetime.now(EASTERN_TIME) >= deadline:
                break
            time.sleep(RETRY_SECONDS)

    anchors = {name: anchor_from_bar(Decimal(str(b[1])), Decimal(str(b[2])), Decimal(str(b[3]))) for name, b in bars.items()}
    offsets, saves = {}, {}
    for name, anchor in anchors.items():
        median = medians.get(name)
        if median is not None:
            offsets[name] = float(anchor) - median
        # The basis shrinks as the futures contract nears expiry, so the check compares with the offset the last days' basis gives for today.
        history = recent_offsets(container.storage, name, FUTURES[name][1], day_open)
        first_range = stats[name][1] if stats.get(name) else 0.0
        saves[name] = auto_save_anchor(
            container.storage, name, day_open.date(), anchor, "yahoo", median, history, dry_run=args.dry_run, first_minute_range=first_range
        )
        logger.info("anchor %s %s: %s (existing %s %s)", name, anchor, saves[name].status, saves[name].existing, saves[name].existing_source)
    message = build_message(day, anchors, offsets, bars, saves, args.test)

    if args.dry_run:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # the server console is cp1252
        print(message)
        return 0
    token = _TelegramSettings().telegram_bot_token
    if token is None or not token.get_secret_value() or not CHAT_FILE.exists():
        logger.error("no bot token or no chat file: message not sent")
        return 1
    chat_id = CHAT_FILE.read_text(encoding="ascii").strip()
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token.get_secret_value()}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=20,
        )
        ok = response.status_code == 200 and response.json().get("ok")
    except httpx.HTTPError as exc:
        logger.error("telegram send failed: %s", type(exc).__name__)
        return 1
    if not ok:
        logger.error("telegram send failed: HTTP %s", response.status_code)
        return 1
    logger.info("sent: %s", " | ".join(f"{k} {v}" for k, v in anchors.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
