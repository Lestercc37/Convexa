from __future__ import annotations

import base64
import sys
from unittest.mock import AsyncMock, patch

import pytest

from backend.adapters.notifications.desktop_notify import (
    _escape_single_quoted,
    notify_windows,
)


class TestEscapeSingleQuoted:
    def test_leaves_plain_text_untouched(self) -> None:
        assert _escape_single_quoted("QQQ queue full") == "QQQ queue full"

    def test_doubles_embedded_single_quotes(self) -> None:
        # The PowerShell single-quoted-literal escaping rule -- a bare
        # single quote inside '...' would otherwise terminate the
        # literal early and corrupt the generated script.
        assert _escape_single_quoted("it's full") == "it''s full"


class TestNotifyWindows:
    @pytest.mark.asyncio
    async def test_is_a_no_op_on_a_non_windows_platform(self) -> None:
        with (
            patch.object(sys, "platform", "linux"),
            patch("asyncio.create_subprocess_exec") as create_subprocess,
        ):
            await notify_windows("Title", "Message")
        create_subprocess.assert_not_called()

    @pytest.mark.asyncio
    async def test_spawns_powershell_with_an_encoded_command_on_windows(self) -> None:
        fake_process = AsyncMock()
        fake_process.wait = AsyncMock(return_value=None)
        with patch.object(sys, "platform", "win32"), patch(
            "asyncio.create_subprocess_exec", return_value=fake_process
        ) as create_subprocess:
            await notify_windows("Convexa alert", "queue is full")

        create_subprocess.assert_called_once()
        args, _kwargs = create_subprocess.call_args
        assert args[0] == "powershell.exe"
        assert "-EncodedCommand" in args
        encoded = args[args.index("-EncodedCommand") + 1]
        script = base64.b64decode(encoded).decode("utf-16-le")
        # The title/message must actually reach the generated script --
        # this is the only place they're carried (see notify_windows'
        # own docstring for why they're embedded here, not passed as
        # separate CLI arguments).
        assert "Convexa alert" in script
        assert "queue is full" in script
        fake_process.wait.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_embeds_title_and_message_with_single_quotes_escaped(self) -> None:
        fake_process = AsyncMock()
        fake_process.wait = AsyncMock(return_value=None)
        with (
            patch.object(sys, "platform", "win32"),
            patch("asyncio.create_subprocess_exec", return_value=fake_process) as create_subprocess,
        ):
            await notify_windows("It's degraded", "consumer's queue is full")

        args, _ = create_subprocess.call_args
        encoded = args[args.index("-EncodedCommand") + 1]
        script = base64.b64decode(encoded).decode("utf-16-le")
        assert "It''s degraded" in script
        assert "consumer''s queue is full" in script

    @pytest.mark.asyncio
    async def test_a_failed_spawn_is_logged_not_raised(self) -> None:
        with patch.object(sys, "platform", "win32"), patch(
            "asyncio.create_subprocess_exec",
            side_effect=OSError("powershell.exe not found"),
        ):
            await notify_windows("Title", "Message")  # must not raise
