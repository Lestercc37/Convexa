"""Daily ES / NQ opening anchors to Lester's Telegram at 09:41 ET (Windows Task Scheduler on the server, weekdays; Lester 2026-10-30 "si").

Why on the server: the chat assistant's scheduled reminders only fire when it is idle, so the 09:41 message arrived 12 minutes late on 2026-10-30.

What it sends (same rule as the manual message since the 2026-10-16 offset fix): from Yahoo's 1-minute ES=F / NQ=F bars (about 10 minutes delayed, so the 09:30 bar exists
from ~09:41), ANCHOR = (high + low + close) / 3 of the 09:30 ET bar, rounded to 0.25 — the number Lester types as the opening price. Convexa's offset = anchor - median of
the first minute of stored SPX / NDX readings; it is reported with a warning when it falls outside the usual ranges.

Skips weekends and days without SPX readings at 09:30 (market holidays). Retries the Yahoo bar every 30 s for up to 4 minutes. The bot token is read from the server's
.env (QLL_TELEGRAM_BOT_TOKEN, written by Lester) and never printed or logged; the chat id is in V:/Convexa/config/telegram_owner_chat.txt.
Usage: python -m backend.scripts.es_nq_anchor_telegram [--dry-run] [--test]
"""

from __future__ import annotations

import argparse
import logging
import statistics
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text

from backend.core.container import build_container
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


def first_minute_median(container, index_symbol: str, day_open: datetime) -> float | None:
    with container.storage.session_factory() as session:
        rows = session.execute(
            text(
                """SELECT m.price FROM market_snapshots m JOIN underlyings u ON u.id = m.underlying_id
                   WHERE u.symbol = :s AND m.time >= :a AND m.time < :b ORDER BY m.time"""
            ),
            {"s": index_symbol, "a": day_open, "b": day_open + timedelta(minutes=1)},
        ).all()
        session.rollback()
    return statistics.median(float(r[0]) for r in rows) if rows else None


def recent_offsets(
    container, future: str, index_symbol: str, before: datetime, sessions: int = 5
) -> list[float]:
    """The offsets of the last `sessions` days Lester typed an anchor: anchor - median of that day's first minute of index readings."""
    with container.storage.session_factory() as session:
        rows = session.execute(
            text(
                """SELECT a.session_date, a.anchor_price FROM future_price_anchors a JOIN underlyings u ON u.id = a.underlying_id
                   WHERE u.symbol = :f AND a.session_date < :d ORDER BY a.session_date DESC LIMIT :n"""
            ),
            {"f": future, "d": before.date(), "n": sessions},
        ).all()
        session.rollback()
    offsets = []
    for session_date, anchor in rows:
        day_open = datetime(
            session_date.year, session_date.month, session_date.day, 9, 30, tzinfo=EASTERN_TIME
        )
        median = first_minute_median(container, index_symbol, day_open)
        if median is not None:
            offsets.append(float(anchor) - median)
    return offsets


def build_message(
    day: str, anchors: dict, offsets: dict, bars: dict, warnings: list[str], test: bool
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
    lines.extend(warnings)
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
    medians = {
        name: first_minute_median(container, spec[1], day_open) for name, spec in FUTURES.items()
    }
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

    anchors = {name: round((b[1] + b[2] + b[3]) / 3 * 4) / 4 for name, b in bars.items()}
    offsets, warnings = {}, []
    for name, anchor in anchors.items():
        median = medians.get(name)
        if median is None:
            continue
        offsets[name] = anchor - median
        margin = FUTURES[name][3]
        # The basis shrinks as the futures contract nears expiry, so a fixed range goes stale: compare with the offsets of the last days typed.
        history = recent_offsets(container, name, FUTURES[name][1], day_open)
        if history:
            usual = statistics.median(history)
            if abs(offsets[name] - usual) > margin:
                warnings.append(
                    f"Ojo: {name} − {FUTURES[name][1]} = {offsets[name]:+.1f}; los últimos días fue {usual:+.1f}. Revísalo antes de escribirlo."
                )
        else:
            low, high = FUTURES[name][2]
            if not (low - margin <= offsets[name] <= high + margin):
                warnings.append(
                    f"Ojo: {name} − {FUTURES[name][1]} = {offsets[name]:+.1f}, fuera de lo habitual ({low:+d} a {high:+d}). Revísalo antes de escribirlo."
                )
    message = build_message(day, anchors, offsets, bars, warnings, args.test)

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
