from __future__ import annotations

import logging

import pytest

from backend.core.frame_gap_monitor import MAX_GAPS_REMEMBERED, FrameGapMonitor

# 2026-10-15 09:47:21 ET = 13:47:21 UTC
WALL_AT_ZERO = 1_792_072_041.0


def monitor(**kwargs) -> tuple[FrameGapMonitor, list[float]]:
    clock = [0.0]
    instance = FrameGapMonitor(
        wall_clock=lambda: WALL_AT_ZERO + clock[0], monotonic_clock=lambda: clock[0], **kwargs
    )
    return instance, clock


def test_a_steady_stream_reports_no_gaps(caplog: pytest.LogCaptureFixture) -> None:
    instance, clock = monitor(summary_every=10.0)
    for step in range(1, 1001):
        clock[0] = step * 0.01
        instance.observe(clock[0])

    with caplog.at_level(logging.INFO, logger="backend.core.frame_gap_monitor"):
        assert instance.maybe_report(10.0)

    assert "1000 frames" in caplog.text
    assert "max gap 10 ms" in caplog.text
    assert "0 gaps >= 250 ms" in caplog.text
    assert "largest" not in caplog.text


def test_a_silent_second_is_reported_with_its_wall_clock_time(caplog: pytest.LogCaptureFixture) -> None:
    instance, clock = monitor(summary_every=10.0)
    for now in (1.0, 1.01, 1.02, 2.2, 2.21):  # the stream goes silent for 1.18 s between 1.02 and 2.2
        clock[0] = now
        instance.observe(now)
    clock[0] = 10.0

    with caplog.at_level(logging.INFO, logger="backend.core.frame_gap_monitor"):
        instance.maybe_report(10.0)

    assert "max gap 1180 ms" in caplog.text
    assert "1 gaps >= 250 ms" in caplog.text
    assert "1180 ms ending 09:47:23.200" in caplog.text


def test_nothing_is_logged_before_the_window_ends(caplog: pytest.LogCaptureFixture) -> None:
    instance, _ = monitor(summary_every=60.0)
    instance.observe(1.0)

    with caplog.at_level(logging.INFO, logger="backend.core.frame_gap_monitor"):
        assert not instance.maybe_report(59.0)

    assert caplog.text == ""


def test_only_the_largest_gaps_are_remembered_and_the_window_resets(caplog: pytest.LogCaptureFixture) -> None:
    instance, clock = monitor(summary_every=10.0)
    now = 0.0
    instance.observe(now)
    for gap in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):  # 7 gaps over the threshold, only 5 are kept
        now += gap
        clock[0] = now
        instance.observe(now)
    clock[0] = 10.0

    with caplog.at_level(logging.INFO, logger="backend.core.frame_gap_monitor"):
        instance.maybe_report(10.0)

    assert "7 gaps >= 250 ms" in caplog.text
    assert caplog.text.count(" ending ") == MAX_GAPS_REMEMBERED
    assert "300 ms ending" not in caplog.text
    assert "900 ms ending" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="backend.core.frame_gap_monitor"):
        instance.maybe_report(20.0)
    assert "0 frames" in caplog.text
    assert "0 gaps >= 250 ms" in caplog.text


def test_the_gap_between_windows_counts_because_the_last_frame_time_survives_the_reset() -> None:
    instance, _ = monitor(summary_every=10.0)
    instance.observe(9.9)
    instance.maybe_report(10.0)

    instance.observe(11.0)  # 1.1 s after the last frame of the previous window

    assert instance._max_gap == pytest.approx(1.1)
