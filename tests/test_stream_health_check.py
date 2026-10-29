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
        assert message and "back to normal" in message
        message, _ = shc.decide_alert(MARKET_NOW + timedelta(minutes=3), [], state)
        assert message is None


class TestToast:
    def test_the_message_is_written_and_the_toast_task_is_started(self, tmp_path, monkeypatch) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(shc.subprocess, "run", lambda cmd, **kwargs: calls.append(cmd))

        shc.notify_toast("CONVEXA STREAM PROBLEM: stalled", tmp_path, "ConvexaStreamAlertToast")

        assert (tmp_path / "stream_alert_message.txt").read_text(encoding="utf-8") == "CONVEXA STREAM PROBLEM: stalled"
        assert calls == [["schtasks", "/Run", "/TN", "ConvexaStreamAlertToast"]]


RESUBSCRIBE_764 = "2026-10-05 09:31:00,000 INFO [x] ThetaStreamHub: resubscribing 764 contracts (1528 messages) on reconnect"
WIDENED_SPY_30 = "2026-10-05 09:40:00,000 INFO [x] Near-the-money set for SPY widened, live-subscribing 30 new contract(s)"
WIDENED_QQQ_12 = "2026-10-05 09:50:00,000 INFO [x] Near-the-money set for QQQ widened, live-subscribing 12 new contract(s)"
RESUBSCRIBE_810 = "2026-10-05 10:00:00,000 INFO [x] ThetaStreamHub: resubscribing 810 contracts (1620 messages) on reconnect"


class TestContractCountNotice:
    def test_count_is_the_last_resubscribe_plus_contracts_added_since(self) -> None:
        log = "\n".join([RESUBSCRIBE_764, WIDENED_SPY_30, WIDENED_QQQ_12])  # noqa: FLY002
        assert shc.count_registered_contracts(log) == 764 + 30 + 12

    def test_a_later_resubscribe_replaces_the_running_count(self) -> None:
        log = "\n".join([RESUBSCRIBE_764, WIDENED_SPY_30, RESUBSCRIBE_810])  # noqa: FLY002
        assert shc.count_registered_contracts(log) == 810

    def test_additions_before_any_resubscribe_are_not_counted(self) -> None:
        assert shc.count_registered_contracts(WIDENED_SPY_30) is None

    def test_below_the_notice_limit_is_fine(self) -> None:
        check = shc.check_contract_count(764)
        assert check.ok and check.severity == "notice"

    def test_close_to_the_plan_limit_is_a_notice_not_an_outage(self) -> None:
        check = shc.check_contract_count(shc.CONTRACT_NOTICE_LIMIT)
        assert not check.ok and check.severity == "notice"
        assert "close to" in check.detail

    def test_over_the_plan_limit_says_so(self) -> None:
        check = shc.check_contract_count(10_500)
        assert not check.ok
        assert "over the plan" in check.detail

    def test_an_unreadable_log_is_not_an_alert(self) -> None:
        assert shc.check_contract_count(None).ok

    def test_a_notice_alone_is_labelled_notice_and_a_mix_is_a_problem(self) -> None:
        notice = shc.check_contract_count(shc.CONTRACT_PLAN_LIMIT + 200)
        message, _ = shc.decide_alert(MARKET_NOW, [notice], {})
        assert message and message.startswith("CONVEXA STREAM NOTICE")
        outage = shc.Check("services", False, "down")
        message, _ = shc.decide_alert(MARKET_NOW, [notice, outage], {})
        assert message and message.startswith("CONVEXA STREAM PROBLEM")


# --- auto-recovery (2026-10-22) ------------------------------------------------------------------------------------------------------------------------------------------------------


def _checks(volume_ok: bool = True, reconnect_ok: bool = True, services_ok: bool = True) -> list:
    from backend.scripts.stream_health_check import Check

    return [Check("volume_flow", volume_ok, "x"), Check("services", services_ok, "x"), Check("reconnect_loop", reconnect_ok, "x")]


def _at(minute: int, hour: int = 14, day: int = 22):
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)  # 10:xx ET (EDT)


def test_auto_restart_waits_for_the_stream_to_fail_for_three_minutes_in_a_row() -> None:
    from backend.scripts.stream_health_check import decide_auto_restart

    state: dict = {}
    decisions = []
    for minute in (0, 1, 2, 3):
        go, state = decide_auto_restart(_at(minute), _checks(reconnect_ok=False), True, state)
        decisions.append(go)
    assert decisions == [False, False, False, True]  # the 4th run is 3 minutes after the first failing one
    assert len(state["auto_restarts"]) == 1 and state["stream_failing_since"] is None


def test_auto_restart_clock_resets_when_the_stream_recovers_or_the_market_closes_or_a_service_is_down() -> None:
    from backend.scripts.stream_health_check import decide_auto_restart

    state: dict = {}
    _, state = decide_auto_restart(_at(0), _checks(volume_ok=False), True, state)
    assert state["stream_failing_since"] is not None
    _, state = decide_auto_restart(_at(1), _checks(), True, state)  # healthy
    assert state["stream_failing_since"] is None
    _, state = decide_auto_restart(_at(2), _checks(volume_ok=False), True, state)
    _, state = decide_auto_restart(_at(3), _checks(volume_ok=False), False, state)  # market closed
    assert state["stream_failing_since"] is None
    _, state = decide_auto_restart(_at(4), _checks(volume_ok=False), True, state)
    go, state = decide_auto_restart(_at(8), _checks(volume_ok=False, services_ok=False), True, state)  # a service is down: restarting the worker would not fix it
    assert go is False and state["stream_failing_since"] is None


def test_auto_restart_has_a_cooldown_and_a_daily_cap() -> None:
    from backend.scripts.stream_health_check import AUTO_RESTART_COOLDOWN_MINUTES, AUTO_RESTART_MAX_PER_DAY, decide_auto_restart

    state: dict = {"auto_restarts": [_at(0).isoformat()], "stream_failing_since": _at(0).isoformat()}
    go, state = decide_auto_restart(_at(5), _checks(reconnect_ok=False), True, state)  # 5 min after the last restart: cooldown
    assert go is False
    go, state = decide_auto_restart(_at(AUTO_RESTART_COOLDOWN_MINUTES + 1), _checks(reconnect_ok=False), True, state)
    assert go is True
    capped = {"auto_restarts": [_at(m).isoformat() for m in range(0, AUTO_RESTART_MAX_PER_DAY * 20, 20)], "stream_failing_since": _at(0).isoformat()}
    go, _ = decide_auto_restart(_at(59), _checks(reconnect_ok=False), True, capped)
    assert go is False  # already restarted the maximum times today
    next_day = {"auto_restarts": [_at(0, day=21).isoformat()] * AUTO_RESTART_MAX_PER_DAY, "stream_failing_since": _at(0).isoformat()}
    go, _ = decide_auto_restart(_at(5), _checks(reconnect_ok=False), True, next_day)
    assert go is True  # yesterday's restarts do not count


def test_run_once_restarts_the_worker_only_with_the_flag(tmp_path, monkeypatch) -> None:
    import backend.scripts.stream_health_check as module

    calls: list[int] = []
    monkeypatch.setattr(module, "restart_worker", lambda: calls.append(1) or True)
    monkeypatch.setattr(module, "read_volume_total", lambda: 5)
    monkeypatch.setattr(module, "windows_service_statuses", lambda: {name: "RUNNING" for name in module.SERVICES})
    monkeypatch.setattr(module, "is_market_open", lambda now: True)
    monkeypatch.setattr(module, "registered_contracts_from_logs", lambda logs_dir: 100)
    monkeypatch.setattr(module, "count_recent_reconnects", lambda *args, **kwargs: 9)  # a reconnect loop
    state = tmp_path / "state.json"
    long_ago = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    state.write_text(__import__("json").dumps({"stream_failing_since": long_ago}))
    module.run_once(tmp_path, state, None, auto_restart=False)
    assert calls == []
    module.run_once(tmp_path, state, None, auto_restart=True)
    assert calls == [1]
    assert "auto-restarted ConvexaWorker" in (tmp_path / "stream_health.log").read_text(encoding="utf-8")


class TestVolumeCorrectionCheck:
    NOW = datetime(2026, 10, 22, 12, 23, 0)  # noqa: DTZ001 -- naive local log time
    FAILED = (
        "2026-10-22 10:30:02,894 WARNING [__main__] Volume correction pass stopped at SPY 2026-10-22: RuntimeError\n"
        "2026-10-22 10:30:02,928 INFO [__main__] Volume correction pass done: {'requests': 0, 'contracts': 0, "
        "'volume': 0, 'seconds': 0.7, 'stopped_because': 'SPY 2026-10-22: RuntimeError'}\n"
    )
    CLEAN = (
        "2026-10-22 12:00:10,490 INFO [__main__] Volume correction pass done: {'requests': 12, 'contracts': 2745, "
        "'volume': 2501742, 'seconds': 7.8, 'stopped_because': None}\n"
    )

    def test_a_pass_that_stopped_early_is_flagged_as_a_notice(self) -> None:
        check = shc.check_volume_correction(self.FAILED, self.NOW)
        assert not check.ok
        assert check.severity == "notice"
        assert "10:30" in check.detail
        assert "SPY 2026-10-22: RuntimeError" in check.detail

    def test_a_later_clean_pass_clears_it(self) -> None:
        assert shc.check_volume_correction(self.FAILED + self.CLEAN, self.NOW).ok

    def test_only_the_last_pass_counts(self) -> None:
        assert not shc.check_volume_correction(self.CLEAN + self.FAILED, self.NOW).ok

    def test_a_stale_failure_is_ignored(self) -> None:
        assert shc.check_volume_correction(self.FAILED, datetime(2026, 10, 23, 9, 41, 0)).ok  # noqa: DTZ001 -- naive local log time

    def test_an_empty_or_missing_log_is_fine(self) -> None:
        assert shc.check_volume_correction("", self.NOW).ok


class TestVolumeCorrectionOneShot:
    FAILED = shc.Check("volume_correction", False, "the 10:30 pass stopped early (SPY: RuntimeError)", "notice")
    OK = shc.Check("volume_correction", True, "last pass finished cleanly", "notice")

    def test_a_failure_is_announced_once(self) -> None:
        message, state = shc.decide_one_shot_notice(self.FAILED, {})
        assert message is not None and "10:30" in message
        again, state = shc.decide_one_shot_notice(self.FAILED, state)
        assert again is None

    def test_a_clean_pass_rearms_it(self) -> None:
        _, state = shc.decide_one_shot_notice(self.FAILED, {})
        cleared, state = shc.decide_one_shot_notice(self.OK, state)
        assert cleared is None
        message, _ = shc.decide_one_shot_notice(self.FAILED, state)
        assert message is not None

    def test_a_different_failure_speaks_again(self) -> None:
        _, state = shc.decide_one_shot_notice(self.FAILED, {})
        other = shc.Check("volume_correction", False, "the 14:00 pass stopped early (QQQ: RuntimeError)", "notice")
        message, _ = shc.decide_one_shot_notice(other, state)
        assert message is not None and "14:00" in message

    def test_it_never_touches_the_other_alert_state(self) -> None:
        _, state = shc.decide_one_shot_notice(self.FAILED, {"alerting_names": ["services"]})
        assert state["alerting_names"] == ["services"]


def test_rediscovery_heartbeat_alarm() -> None:
    from datetime import datetime as _dt

    from backend.scripts.stream_health_check import check_rediscovery_heartbeat, last_rediscovery_heartbeat

    log = (
        "2026-10-29 10:00:01,1 INFO [x] Contract rediscovery alive: pass 1100, 640 contracts registered\n"
        "2026-10-29 10:10:02,1 INFO [x] Contract rediscovery alive: pass 1110, 641 contracts registered\n"
        "2026-10-29 10:12:00,1 INFO [x] something else\n"
    )
    last = last_rediscovery_heartbeat(log)
    assert last == _dt(2026, 10, 29, 10, 10, 2)
    assert check_rediscovery_heartbeat(last, _dt(2026, 10, 29, 10, 20, 0), True, 50).ok
    stale = check_rediscovery_heartbeat(last, _dt(2026, 10, 29, 10, 30, 0), True, 60)
    assert not stale.ok and "silent for 20 min" in stale.detail
    assert check_rediscovery_heartbeat(None, _dt(2026, 10, 29, 9, 35), True, 5).ok  # opening grace
    assert check_rediscovery_heartbeat(None, _dt(2026, 10, 29, 20, 0), False, 600).ok  # market closed
    assert not check_rediscovery_heartbeat(None, _dt(2026, 10, 29, 11, 0), True, 90).ok
