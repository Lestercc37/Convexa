"""Minimal Telegram Bot API client for the EMA cross alerts (sendMessage + getUpdates long polling).

The bot token comes from Settings.telegram_bot_token (QLL_TELEGRAM_BOT_TOKEN in the server's .env, written there by Lester himself). It is part of every request URL, so it
must never reach a log: errors are reported as "telegram <method> failed: <status or exception class>" only, never with the exception text or the URL.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)
API = "https://api.telegram.org"


class TelegramError(Exception):
    """Carries only the method and a status / exception class name, never the URL (it contains the token)."""


class TelegramClient:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._base = f"{API}/bot{token}"
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=10.0))

    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            response = await self._client.post(f"{self._base}/{method}", json=payload)
        except httpx.HTTPError as exc:
            raise TelegramError(f"telegram {method} failed: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise TelegramError(f"telegram {method} failed: HTTP {response.status_code}")
        body = response.json()
        if not body.get("ok"):
            raise TelegramError(f"telegram {method} failed: {body.get('error_code')}")
        return body.get("result")

    async def send_message(self, chat_id: int, text: str) -> None:
        await self._call(
            "sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        )

    async def get_updates(
        self, offset: int | None, timeout_seconds: int = 25
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout_seconds, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        return await self._call("getUpdates", payload) or []

    async def bot_username(self) -> str | None:
        me = await self._call("getMe", {})
        return me.get("username") if isinstance(me, dict) else None

    async def close(self) -> None:
        await self._client.aclose()
