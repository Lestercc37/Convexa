"""Read-only dead-stream alarm. Meant to be run every minute by Windows Task
Scheduler on the server (or with --loop); it never writes to the database and
never talks to ThetaData or to the relay ports.

Why it exists: on 2026-10-03/04 the worker's stream sat dead for ~20 hours in
a reconnect loop and nothing told anyone. Three independent signals, so one
blind spot cannot hide an outage:

  volume_flow     During market hours the sum of contract_cumulative_volume
                  (written every 15s by the stream processor) must change.
                  Unchanged for STALL_MINUTES = option trades are not flowing.
  services        The stream's Windows services are all RUNNING.
  reconnect_loop  worker.log shows RECONNECT_LOOP_COUNT or more
                  "stream disconnected, reconnecting" lines in the last
                  RECONNECT_LOOP_WINDOW_MINUTES -- the signature of both the
                  04-Oct lock-step loop and a Terminal flapping.

And one informational notice (not an outage):

  contract_count  The worker streams TRADE and QUOTE for every registered
                  contract and the plan (Options: STANDARD) documents 10,000
                  streamable contracts for quotes and 15,000 for trades (the
                  1,000 / 2,000 table on the same page is the STOCKS section;
                  corrected 2026-10-05). The registered set only
                  grows during a session (and roughly doubles around the 16:00
                  close); it was 1,440-1,668 on several mornings when the
                  worker carried it over. Reconstructed from worker.log (last
                  "resubscribing N contracts" plus every later "widened,
                  live-subscribing K new contract(s)") and noticed at
                  CONTRACT_NOTICE_LIMIT. The Terminal has never answered
                  MAX_STREAMS_REACHED in our logs, so what happens past the
                  cap (rejected, or silently not delivered) is unknown.

Alerts go to logs/stream_health.log always, and optionally to:
  --toast-task NAME   a Windows toast on the server's desktop: the message is
                      written to logs/stream_alert_message.txt and the
                      scheduled task NAME (running backend/scripts/show_toast.ps1
                      as the logged-in desktop user) is started
  ALERT_NTFY_URL      e.g. https://ntfy.sh/<secret-topic>, plain-text POST
  ALERT_WEBHOOK_URL   JSON POST with "text" and "content" keys, Slack/Discord
Nothing is sent anywhere unless one of them is set.

    python -m backend.scripts.stream_health_check            # one check
    python -m backend.scripts.stream_health_check --loop 60  # forever

Known limit: a weekday market holiday looks like a stalled market. The next
one is Thanksgiving (2026-11-26); mute the task that day.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.domain.use_cases.market_hours import is_market_open

EASTERN = ZoneInfo("America/New_York")
CONTRACT_NOTICE_LIMIT = 9500
CONTRACT_PLAN_LIMIT = 10_000  # Options STANDARD quote contracts (the tighter of 10,000 / 15,000)
STALL_MINUTES = 3
GRACE_AFTER_OPEN_MINUTES = 10
RECONNECT_LOOP_COUNT = 6
RECONNECT_LOOP_WINDOW_MINUTES = 5
REALERT_MINUTES = 30
SERVICES = ("ConvexaWorker", "ConvexaStreamProcessor", "ConvexaWhaleAlerts", "ConvexaThetaTerminal")
LOG_TAIL_BYTES = 512 * 1024
_LOG_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ ")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    severity: str = "alert"  # "notice" = informational, never called an outage


# ---------------------------------------------------------------- pure logic
def minutes_since_open(now_utc: datetime) -> float:
    local = now_utc.astimezone(EASTERN)
    open_at = local.replace(hour=9, minute=30, second=0, microsecond=0)
    return (local - open_at).total_seconds() / 60


def check_volume_flow(
    now: datetime, market_open: bool, total: int | None, state: dict
) -> tuple[Check, dict]:
    """Returns the check and the new state. `state` carries the last seen total
    and when it last changed."""
    changed_at = state.get("volume_changed_at")
    previous = state.get("volume_total")
    if total is None:
        return Check("volume_flow", False, "could not read contract_cumulative_volume"), state
    if previous is None or total != previous or changed_at is None or not market_open:
        # progress, first run, or market closed (nothing to expect): reset the clock
        new_state = {**state, "volume_total": total, "volume_changed_at": now.isoformat()}
        return Check("volume_flow", True, "market closed or volume moving"), new_state
    stalled_for = (now - datetime.fromisoformat(changed_at)).total_seconds() / 60
    in_grace = minutes_since_open(now) < GRACE_AFTER_OPEN_MINUTES
    if stalled_for >= STALL_MINUTES and not in_grace:
        return (
            Check("volume_flow", False, f"cumulative option volume unchanged for {stalled_for:.0f} min during market hours"),
            state,
        )
    return Check("volume_flow", True, f"volume unchanged for {stalled_for:.1f} min (limit {STALL_MINUTES})"), state


def check_services(statuses: dict[str, str]) -> Check:
    down = {name: status for name, status in statuses.items() if status != "RUNNING"}
    if down:
        return Check("services", False, "not running: " + ", ".join(f"{n}={s}" for n, s in down.items()))
    return Check("services", True, "all running")


def count_recent_reconnects(log_text: str, now_local: datetime, window_minutes: int) -> int:
    cutoff = now_local - timedelta(minutes=window_minutes)
    count = 0
    for line in log_text.splitlines():
        if "stream disconnected, reconnecting" not in line:
            continue
        match = _LOG_TIME.match(line)
        if match and datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S") >= cutoff:  # noqa: DTZ007 -- worker.log stamps are naive local time
            count += 1
    return count


def check_reconnect_loop(reconnects: int) -> Check:
    if reconnects >= RECONNECT_LOOP_COUNT:
        return Check(
            "reconnect_loop",
            False,
            f"{reconnects} worker reconnects in the last {RECONNECT_LOOP_WINDOW_MINUTES} min",
        )
    return Check("reconnect_loop", True, f"{reconnects} reconnects in the last {RECONNECT_LOOP_WINDOW_MINUTES} min")


_RESUBSCRIBE = re.compile(r"resubscribing (\d+) contracts")
_WIDENED = re.compile(r"widened, live-subscribing (\d+) new contract")


def count_registered_contracts(log_text: str) -> int | None:
    """Contracts the worker has registered right now: the count at its last
    resubscribe plus every contract live-added since. None if the log has no
    resubscribe line at all."""
    registered: int | None = None
    for line in log_text.splitlines():
        match = _RESUBSCRIBE.search(line)
        if match:
            registered = int(match.group(1))
            continue
        match = _WIDENED.search(line)
        if match and registered is not None:
            registered += int(match.group(1))
    return registered


def check_contract_count(count: int | None) -> Check:
    if count is None:
        return Check("contract_count", True, "unknown (no resubscribe line in the log)", "notice")
    if count > CONTRACT_PLAN_LIMIT:
        return Check(
            "contract_count",
            False,
            f"{count} contracts registered, over the plan's {CONTRACT_PLAN_LIMIT} streamable per type "
            "(what the Terminal does past the cap is unknown)",
            "notice",
        )
    if count >= CONTRACT_NOTICE_LIMIT:
        return Check(
            "contract_count",
            False,
            f"{count} contracts registered, close to the plan's {CONTRACT_PLAN_LIMIT} streamable per type",
            "notice",
        )
    return Check("contract_count", True, f"{count} contracts registered (plan {CONTRACT_PLAN_LIMIT})", "notice")


def decide_alert(now: datetime, failing: list[Check], state: dict) -> tuple[str | None, dict]:
    """(message or None, new state). Alerts on a new/changed failure set, again
    every REALERT_MINUTES while failing, and once on recovery."""
    names = sorted(c.name for c in failing)
    last_names = state.get("alerting_names", [])
    last_at = state.get("last_alert_at")
    if failing:
        due = (
            names != last_names
            or last_at is None
            or (now - datetime.fromisoformat(last_at)).total_seconds() >= REALERT_MINUTES * 60
        )
        if not due:
            return None, state
        label = "NOTICE" if all(c.severity == "notice" for c in failing) else "PROBLEM"
        message = f"CONVEXA STREAM {label}: " + "; ".join(f"{c.name}: {c.detail}" for c in failing)
        return message, {**state, "alerting_names": names, "last_alert_at": now.isoformat()}
    if last_names:
        return "Convexa stream: all checks back to normal.", {**state, "alerting_names": [], "last_alert_at": None}
    return None, state


# ------------------------------------------------------------------ impure
def read_log_text(path: Path, max_bytes: int = 12 * 1024 * 1024) -> str:
    """Up to the last max_bytes of a log; worker.log rotates at ~9.5 MB so this
    is normally the whole file."""
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes))
        return handle.read().decode("utf-8", "replace")


def registered_contracts_from_logs(logs_dir: Path) -> int | None:
    current = count_registered_contracts(read_log_text(logs_dir / "worker.log"))
    if current is not None:
        return current
    # worker.log just rotated: the last resubscribe line is in the previous file
    return count_registered_contracts(read_log_text(logs_dir / "worker.log.1") + read_log_text(logs_dir / "worker.log"))


def read_log_tail(path: Path) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - LOG_TAIL_BYTES))
        return handle.read().decode("utf-8", "replace")


def windows_service_statuses() -> dict[str, str]:
    if sys.platform != "win32":
        return {}
    statuses: dict[str, str] = {}
    for name in SERVICES:
        result = subprocess.run(["sc", "query", name], capture_output=True, text=True, timeout=20, check=False)
        match = re.search(r"STATE\s*:\s*\d+\s+(\w+)", result.stdout)
        statuses[name] = match.group(1) if match else "UNKNOWN"
    return statuses


def read_volume_total() -> int | None:
    from sqlalchemy import text

    from backend.core.settings import get_settings
    from backend.infrastructure.database.engine import create_sync_engine

    engine = create_sync_engine(get_settings().database_url)
    try:
        with engine.connect() as connection:
            value = connection.execute(text("SELECT COALESCE(SUM(volume), 0) FROM contract_cumulative_volume")).scalar()
            return int(value or 0)
    except Exception:  # noqa: BLE001 -- reported as a failed check, not a crash
        return None
    finally:
        engine.dispose()


def notify_toast(message: str, logs_dir: Path, task_name: str) -> None:
    (logs_dir / "stream_alert_message.txt").write_text(message, encoding="utf-8")
    subprocess.run(["schtasks", "/Run", "/TN", task_name], capture_output=True, timeout=20, check=True)


def notify(message: str) -> None:
    ntfy = os.environ.get("ALERT_NTFY_URL")
    webhook = os.environ.get("ALERT_WEBHOOK_URL")
    if ntfy:
        request = urllib.request.Request(ntfy, data=message.encode("utf-8"), method="POST")
        urllib.request.urlopen(request, timeout=10).close()
    if webhook:
        body = json.dumps({"text": message, "content": message}).encode("utf-8")
        request = urllib.request.Request(webhook, data=body, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(request, timeout=10).close()


def run_once(logs_dir: Path, state_path: Path, toast_task: str | None = None) -> list[Check]:
    now = datetime.now(UTC)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    market_open = is_market_open(now)
    volume_check, state = check_volume_flow(now, market_open, read_volume_total(), state)
    checks = [
        volume_check,
        check_services(windows_service_statuses()),
        check_reconnect_loop(
            count_recent_reconnects(
                read_log_tail(logs_dir / "worker.log"),
                datetime.now(),  # noqa: DTZ005 -- compared against naive local log stamps
                RECONNECT_LOOP_WINDOW_MINUTES,
            )
        ),
        check_contract_count(registered_contracts_from_logs(logs_dir)),
    ]
    failing = [c for c in checks if not c.ok]
    message, state = decide_alert(now, failing, state)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state))
    stamp = now.astimezone(EASTERN).strftime("%Y-%m-%d %H:%M:%S ET")
    line = f"{stamp} market_open={market_open} " + " | ".join(
        f"{c.name}={'OK' if c.ok else 'FAIL'} ({c.detail})" for c in checks
    )
    with (logs_dir / "stream_health.log").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        if message:
            handle.write(f"{stamp} ALERT: {message}\n")
    print(line)
    if message:
        print("ALERT:", message)
        try:
            if toast_task:
                notify_toast(message, logs_dir, toast_task)
            notify(message)
        except Exception as exc:  # noqa: BLE001 -- a failed send must not hide the check itself
            print("alert delivery failed:", exc)
    return checks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs-dir", default="logs")
    parser.add_argument("--state", default="logs/stream_health_state.json")
    parser.add_argument("--loop", type=int, default=0, help="seconds between checks; 0 = run once")
    parser.add_argument("--toast-task", default=None, help="scheduled task that shows a Windows toast")
    args = parser.parse_args()
    logs_dir = Path(args.logs_dir)
    while True:
        checks = run_once(logs_dir, Path(args.state), args.toast_task)
        if not args.loop:
            sys.exit(0 if all(c.ok for c in checks) else 1)
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
