from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.scripts import stream_health_check as shc

# Monday 2026-10-05 10:30 ET = 14:30 UTC, well past the post-open grace period.
MARKET_NOW = datetime(2026, 10, 5, 14, 30, tzinfo=UTC)


class TestVolumeFlow:
    def test_unchanged_volume_for_longer_than_the_limit_during_market_hours_fails(self) -> None:
        state = {"volume_total": 500, "volume_changed_at": (MARKET_NOW - timedelta(minutes=5)).isoformat()}
        check, new_state = shc.check_volume_flow(MARKET_NOW, True, 500, state)
        assert not check.ok
        assert "unchanged for 5 min" in check.detail
        assert new_state == state, "a stall must not reset its own clock"

    def test_volume_that_moved_resets_the_clock(self) -> None:
        state = {"volume_total": 500, "volume_changed_at": (MARKET_NOW - timedelta(minutes=30)).isoformat()}
        check, new_state = shc.check_volume_flow(MARKET_NOW, True, 640, state)
        assert check.ok
        assert new_state["volume_total"] == 640
        assert new_state["volume_changed_at"] == MARKET_NOW.isoformat()

    def test_a_short_stall_is_not_an_alert(self) -> None:
        state = {"volume_total": 500, "volume_changed_at": (MARKET_NOW - timedelta(minutes=1)).isoformat()}
        check, _ = shc.check_volume_flow(MARKET_NOW, True, 500, state)
        assert check.ok

    def test_market_closed_never_alerts_and_keeps_the_clock_fresh(self) -> None:
        state = {"volume_total": 500, "volume_changed_at": (MARKET_NOW - timedelta(hours=20)).isoformat()}
        check, new_state = shc.check_volume_flow(MARKET_NOW, False, 500, state)
        assert check.ok
        assert new_state["volume_changed_at"] == MARKET_NOW.isoformat()

    def test_the_first_minutes_after_the_open_are_a_grace_period(self) -> None:
        at_open = datetime(2026, 10, 5, 13, 35, tzinfo=UTC)  # 09:35 ET
        state = {"volume_total": 500, "volume_changed_at": (at_open - timedelta(minutes=20)).isoformat()}
        check, _ = shc.check_volume_flow(at_open, True, 500, state)
        assert check.ok

    def test_an_unreadable_table_is_a_failed_check_not_a_crash(self) -> None:
        check, _ = shc.check_volume_flow(MARKET_NOW, True, None, {})
        assert not check.ok


class TestServicesAndReconnectLoop:
    def test_any_service_not_running_fails(self) -> None:
        check = shc.check_services({"ConvexaWorker": "RUNNING", "ConvexaThetaTerminal": "STOPPED"})
        assert not check.ok
        assert "ConvexaThetaTerminal=STOPPED" in check.detail

    def test_all_running_passes(self) -> None:
        assert shc.check_services({"ConvexaWorker": "RUNNING"}).ok

    def test_counts_only_reconnect_lines_inside_the_window(self) -> None:
        now = datetime(2026, 10, 4, 11, 10, 0)  # noqa: DTZ001 -- log stamps are naive local time
        log = (
            "2026-10-04 11:09:06,020 ERROR [x] ThetaData stream disconnected, reconnecting in 60s\n"
            "2026-10-04 11:08:06,020 ERROR [x] ThetaData stream disconnected, reconnecting in 60s\n"
            "2026-10-04 11:02:06,020 ERROR [x] ThetaData stream disconnected, reconnecting in 60s\n"  # too old
            "2026-10-04 11:09:30,000 INFO [x] some other line\n"
            "Traceback (most recent call last):"
        )
        assert shc.count_recent_reconnects(log, now, 5) == 2

    def test_the_20_hour_loop_trips_the_check_in_five_minutes(self) -> None:
        # one reconnect per minute: 5 in a 5-minute window is below 6, 6 trips it
        assert shc.check_reconnect_loop(5).ok
        assert not shc.check_reconnect_loop(6).ok


class TestAlertDecision:
    FAIL = (shc.Check("volume_flow", False, "stalled"),)

    def test_first_failure_alerts(self) -> None:
        message, state = shc.decide_alert(MARKET_NOW, list(self.FAIL), {})
        assert message and "volume_flow" in message
        assert state["alerting_names"] == ["volume_flow"]

    def test_the_same_failure_does_not_repeat_every_minute(self) -> None:
        _, state = shc.decide_alert(MARKET_NOW, list(self.FAIL), {})
        message, _ = shc.decide_alert(MARKET_NOW + timedelta(minutes=1), list(self.FAIL), state)
        assert message is None

    def test_it_repeats_after_the_realert_interval(self) -> None:
        _, state = shc.decide_alert(MARKET_NOW, list(self.FAIL), {})
        message, _ = shc.decide_alert(MARKET_NOW + timedelta(minutes=shc.REALERT_MINUTES + 1), list(self.FAIL), state)
        assert message

    def test_a_different_failure_alerts_immediately(self) -> None:
        _, state = shc.decide_alert(MARKET_NOW, list(self.FAIL), {})
        other = [shc.Check("services", False, "down")]
        message, _ = shc.decide_alert(MARKET_NOW + timedelta(minutes=1), [*self.FAIL, *other], state)
        assert message and "services" in message

    def test_recovery_is_announced_once(self) -> None:
        _, state = shc.decide_alert(MARKET_NOW, list(self.FAIL), {})
        message, state = shc.decide_alert(MARKET_NOW + timedelta(minutes=2), [], state)
        assert message and "recovered" in message
        message, _ = shc.decide_alert(MARKET_NOW + timedelta(minutes=3), [], state)
        assert message is None


class TestToast:
    def test_the_message_is_written_and_the_toast_task_is_started(self, tmp_path, monkeypatch) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(shc.subprocess, "run", lambda cmd, **kwargs: calls.append(cmd))

        shc.notify_toast("CONVEXA STREAM PROBLEM: stalled", tmp_path, "ConvexaStreamAlertToast")

        assert (tmp_path / "stream_alert_message.txt").read_text(encoding="utf-8") == "CONVEXA STREAM PROBLEM: stalled"
        assert calls == [["schtasks", "/Run", "/TN", "ConvexaStreamAlertToast"]]
