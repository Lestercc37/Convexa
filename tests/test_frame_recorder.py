from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from backend.adapters.providers.thetadata import frame_recorder
from backend.adapters.providers.thetadata.frame_recorder import FrameRecorder, load_request
from backend.adapters.providers.thetadata.provider import ThetaStreamHub
from backend.domain.use_cases.market_hours import EASTERN_TIME


def _wait_for_writer(recorder: FrameRecorder) -> None:
    recorder._thread.join(timeout=5)


class TestFrameRecorder:
    def test_records_only_inside_the_window_one_line_per_frame(self, tmp_path: Path) -> None:
        out = tmp_path / "c.tsv"
        recorder = FrameRecorder(out, start_epoch=1000.0, seconds=10)
        recorder.record('{"a":1}', now=999.0)  # before the window
        recorder.record('{"a":2}', now=1000.5)
        recorder.record('{"a":3}', now=1005.0)
        recorder.record('{"a":4}', now=1010.0)  # at/after the end: finishes
        recorder.record('{"a":5}', now=1011.0)  # ignored
        _wait_for_writer(recorder)

        assert recorder.finished and recorder.frames == 2
        assert out.read_text(encoding="utf-8").splitlines() == [
            '1000500\t{"a":2}',
            '1005000\t{"a":3}',
        ]

    def test_large_captures_are_written_in_chunks(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(frame_recorder, "FLUSH_EVERY_FRAMES", 100)
        out = tmp_path / "c.tsv"
        recorder = FrameRecorder(out, start_epoch=0.0, seconds=1e9)
        for i in range(250):
            recorder.record("{}", now=1.0 + i)
        recorder.finish()
        _wait_for_writer(recorder)
        assert len(out.read_text(encoding="utf-8").splitlines()) == 250

    def test_the_size_cap_stops_the_capture(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(frame_recorder, "MAX_BYTES", 50)
        recorder = FrameRecorder(tmp_path / "c.tsv", start_epoch=0.0, seconds=1e9)
        for i in range(100):
            recorder.record('{"padding":"xxxxxxxxxxxxxxxx"}', now=1.0 + i)
        assert recorder.finished and recorder.frames < 100
        _wait_for_writer(recorder)


class TestLoadRequest:
    NOW = datetime(2026, 10, 6, 9, 20, tzinfo=EASTERN_TIME)

    def _request(self, tmp_path: Path, **overrides: object) -> None:
        body = {"date": "2026-10-06", "start_et": "09:30:00", "seconds": 90, **overrides}
        (tmp_path / frame_recorder.REQUEST_FILENAME).write_text(json.dumps(body), encoding="utf-8")

    def test_no_request_file_means_no_recorder(self, tmp_path: Path) -> None:
        assert load_request(tmp_path, self.NOW) is None

    def test_a_future_request_arms_a_recorder_for_that_window(self, tmp_path: Path) -> None:
        self._request(tmp_path)
        recorder = load_request(tmp_path, self.NOW)
        assert recorder is not None
        assert recorder._start == datetime(2026, 10, 6, 9, 30, tzinfo=EASTERN_TIME).timestamp()
        assert recorder._end - recorder._start == 90
        recorder.finish()
        _wait_for_writer(recorder)
        assert (tmp_path / "stream_capture_20261006_093000.tsv").exists()

    def test_a_window_that_is_already_over_is_renamed_done_and_ignored(self, tmp_path: Path) -> None:
        self._request(tmp_path, date="2026-10-05")
        assert load_request(tmp_path, self.NOW) is None
        assert (tmp_path / (frame_recorder.REQUEST_FILENAME + ".done")).exists()

    def test_a_garbage_request_is_renamed_invalid_not_retried(self, tmp_path: Path) -> None:
        (tmp_path / frame_recorder.REQUEST_FILENAME).write_text("not json", encoding="utf-8")
        assert load_request(tmp_path, self.NOW) is None
        assert (tmp_path / (frame_recorder.REQUEST_FILENAME + ".invalid")).exists()

    def test_the_duration_is_capped(self, tmp_path: Path) -> None:
        self._request(tmp_path, seconds=999999)
        recorder = load_request(tmp_path, self.NOW)
        assert recorder is not None and recorder._end - recorder._start == frame_recorder.MAX_SECONDS
        recorder.finish()
        _wait_for_writer(recorder)


class TestHubIntegration:
    @pytest.mark.asyncio
    async def test_frames_reach_the_recorder_and_the_queue_is_unaffected(self, tmp_path: Path) -> None:
        stream = ThetaStreamHub("ws://127.0.0.1:1/x", httpx.Client(base_url="http://127.0.0.1:1"))
        recorder = FrameRecorder(tmp_path / "c.tsv", start_epoch=0.0, seconds=1e12)
        stream._frame_recorder = recorder
        stream._enqueue_raw_frame('{"header":{"type":"QUOTE"}}')
        assert stream._message_queue.qsize() == 1
        recorder.finish()
        _wait_for_writer(recorder)
        assert recorder.frames == 1

    @pytest.mark.asyncio
    async def test_the_watcher_arms_a_request_and_disarms_it_when_finished(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import backend.adapters.providers.thetadata.provider as provider_module

        monkeypatch.chdir(tmp_path)
        (tmp_path / "logs").mkdir()
        monkeypatch.setattr(provider_module, "CAPTURE_POLL_SECONDS", 0.02)
        started = datetime.now(EASTERN_TIME) - timedelta(seconds=60)
        (tmp_path / "logs" / frame_recorder.REQUEST_FILENAME).write_text(
            json.dumps({"date": started.strftime("%Y-%m-%d"), "start_et": started.strftime("%H:%M:%S"), "seconds": 500}),
            encoding="utf-8",
        )
        stream = ThetaStreamHub("ws://127.0.0.1:1/x", httpx.Client(base_url="http://127.0.0.1:1"))
        task = asyncio.create_task(stream._watch_capture_request())
        await asyncio.sleep(0.15)
        assert stream._frame_recorder is not None
        stream._frame_recorder.finish()
        await asyncio.sleep(0.15)
        task.cancel()
        assert stream._frame_recorder is None
        assert (tmp_path / "logs" / (frame_recorder.REQUEST_FILENAME + ".done")).exists()


class TestHubWebSocketCompression:
    @pytest.mark.asyncio
    async def test_the_hub_does_not_offer_permessage_deflate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Eduardo (ThetaData), 2026-10-06: the Terminal only uses
        permessage-deflate when the client offers it, and on loopback it is
        pure CPU. The hub must connect with compression=None."""
        import backend.adapters.providers.thetadata.provider as provider_module

        seen: dict[str, object] = {}

        class _Stop(Exception):
            pass

        def fake_connect(url: str, **kwargs: object):
            seen.update(kwargs)
            raise _Stop

        monkeypatch.setattr(provider_module.websockets, "connect", fake_connect)
        stream = ThetaStreamHub("ws://127.0.0.1:1/x", httpx.Client(base_url="http://127.0.0.1:1"))
        with pytest.raises(_Stop):
            await stream._connect_and_consume()
        assert "compression" in seen and seen["compression"] is None
