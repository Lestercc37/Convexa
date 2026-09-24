"""Fire-and-forget Windows desktop notifications for operator-facing alerts.

Convexa runs locally on the operator's own machine, not behind Slack/
email/SMS -- a native OS notification is the fastest way to reach them
regardless of whether they're looking at a terminal or the dashboard at
that moment. Windows only, since that's the only platform this project
runs on -- a no-op everywhere else (never raises), so a future non-
Windows dev/CI environment never trips over this.

Uses a NotifyIcon balloon tip (System.Windows.Forms), not the newer WinRT
toast API (Windows.UI.Notifications.ToastNotificationManager) -- the WinRT
path needs a registered AppUserModelID to actually render when launched
from a bare `powershell.exe` script on modern Windows, and silently does
nothing otherwise (confirmed against this project's own launch shape: no
Start Menu shortcut/packaged app identity exists to register one against).
NotifyIcon needs no such registration and has reliably worked from a
one-shot script for years.

A standalone helper, not an INotificationService implementation (see
backend/adapters/notifications/noop.py) -- that port's own
notify(FlowEvent | GammaAggregate) contract is for domain trading
events, not this module's operational/system-health concern (a
consumer queue silently dropping messages), and it isn't wired into
container.py yet regardless. This lives alongside it because it's the
same kind of thing (an adapter that reaches an external notification
channel), not because it implements that same port.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import sys

logger = logging.getLogger(__name__)

# Long enough to comfortably read a short alert, short enough that the
# spawned powershell.exe process (one per notification) doesn't linger --
# a NotifyIcon disposed immediately after ShowBalloonTip never renders, so
# the process must stay alive at least this long.
_BALLOON_VISIBLE_SECONDS = 8

_BALLOON_SCRIPT_TEMPLATE = """
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$icon = New-Object System.Windows.Forms.NotifyIcon
$icon.Icon = [System.Drawing.SystemIcons]::Warning
$icon.Visible = $true
$icon.ShowBalloonTip(10000, '{title}', '{message}', [System.Windows.Forms.ToolTipIcon]::Warning)
Start-Sleep -Seconds {visible_seconds}
$icon.Dispose()
"""


def _escape_single_quoted(text: str) -> str:
    """PowerShell single-quoted strings ('...') are the literal kind --
    the only character that needs escaping inside one is a single quote
    itself, doubled ('' inside the literal). This project's own callers
    only ever pass internally-generated text (fixed symbol/kind names,
    counts) with no apostrophes today, but escaping is cheap and makes
    this correct regardless of what a future caller's title/message
    contains, rather than relying on that staying true."""
    return text.replace("'", "''")


async def notify_windows(title: str, message: str) -> None:
    """Shows a Windows balloon-tip notification with `title`/`message`.

    Fire-and-forget by design -- callers on a hot/timing-sensitive path
    (see ThetaStreamHub._maybe_alert_queue_saturation) should schedule
    this via asyncio.create_task(...) rather than awaiting it inline, so
    a slow-to-spawn powershell.exe process can never stall that path.
    Awaiting it directly (e.g. from a test, or a non-hot-path caller) is
    still safe -- this coroutine's own work (spawning the process,
    waiting for it to exit) never blocks the event loop either way.

    `title`/`message` are formatted directly into the script text (as a
    single-quoted PowerShell literal, escaped via _escape_single_quoted)
    rather than passed as separate process/script arguments -- confirmed
    `-EncodedCommand` has no documented, reliable mechanism for binding
    trailing command-line arguments to named script parameters the way
    `-File script.ps1 -Title x` does, so embedding them in the encoded
    script itself is the only approach actually guaranteed to work.

    Errors are logged, never raised -- a failed notification (no
    powershell.exe on PATH, a non-Windows host, the balloon tip API
    unavailable in whatever session this runs under) must never take
    down the caller's own real work over a best-effort alert.
    """
    if sys.platform != "win32":
        return
    script = _BALLOON_SCRIPT_TEMPLATE.format(
        title=_escape_single_quoted(title),
        message=_escape_single_quoted(message),
        visible_seconds=_BALLOON_VISIBLE_SECONDS,
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        process = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-EncodedCommand",
            encoded,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.wait()
    except OSError:
        logger.exception("Failed to show Windows desktop notification %r", title)
