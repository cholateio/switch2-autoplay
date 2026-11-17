import contextlib
import logging
import threading
import time

import av
import av.logging
import numpy as np
import pytest

from autoplay.core import clock
from autoplay.core.capture import device
from autoplay.core.capture.base import CaptureError
from autoplay.core.capture.device import (
    AvCapture,
    DeviceSource,
    open_capture,
    parse_device_list,
    resolve_device,
)


# Stands in for an open capture device; frame n is an 8x8 image filled with n % 256.
class FakeCapture:
    fourcc = "YUY2"

    def __init__(self, *, limit=None, delay=0.001):
        self.limit = limit
        self.delay = delay
        self.failing = False
        self.produced = 0
        self.released = False

    def read(self):
        time.sleep(self.delay)
        if self.failing or (self.limit is not None and self.produced >= self.limit):
            return False, None
        self.produced += 1
        return True, np.full((8, 8, 3), self.produced % 256, dtype=np.uint8)

    def release(self):
        self.released = True


def make_source(fake, **kwargs):
    calls = []

    def opener(*args):
        calls.append(args)
        return fake

    return DeviceSource(0, opener=opener, **kwargs), calls


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_latest_returns_newest_frame_only():
    fake = FakeCapture(limit=50)
    src, _ = make_source(fake)
    with src:
        src.start()
        assert wait_until(lambda: src.stats().frames == 50)
        frame = src.latest()
        assert frame.seq == 50
        assert int(frame.bgr[0, 0, 0]) == 50


def test_wait_next_blocks_until_newer_seq():
    fake = FakeCapture(delay=0.005)
    src, _ = make_source(fake)
    with src:
        src.start()
        f = src.latest()
        nxt = src.wait_next(after_seq=f.seq, timeout=1)
        assert nxt is not None
        assert nxt.seq > f.seq
        assert nxt.ts >= f.ts


def test_wait_next_times_out_returns_none():
    fake = FakeCapture(limit=1)
    src, _ = make_source(fake)
    with src:
        src.start()
        f = src.latest()
        t0 = time.monotonic()
        assert src.wait_next(after_seq=f.seq, timeout=0.1) is None
        assert time.monotonic() - t0 >= 0.09


def test_start_raises_when_device_cannot_open():
    def opener(index, options):
        raise OSError("I/O error")

    src = DeviceSource(0, opener=opener)
    with pytest.raises(CaptureError) as exc:
        src.start()
    assert "index 0" in str(exc.value)
    assert "1920x1080 YUY2 at 60 fps" in str(exc.value)
    assert "I/O error" in str(exc.value)
    src.close()


def test_opener_capture_error_is_not_rewrapped():
    def opener(index, options):
        raise CaptureError("no capture device index 3; found none")

    with pytest.raises(CaptureError, match="found none") as exc:
        open_capture(3, width=1920, height=1080, fps=60, fourcc="YUY2", opener=opener)
    assert "cannot open" not in str(exc.value)


def test_start_raises_when_no_first_frame(monkeypatch):
    monkeypatch.setattr(device, "_FIRST_FRAME_TIMEOUT_S", 0.1)
    fake = FakeCapture(limit=0)
    src, _ = make_source(fake)
    with pytest.raises(CaptureError) as exc:
        src.start()
    assert "index 0" in str(exc.value)
    assert "signal" in str(exc.value)
    assert fake.released


def test_start_requests_the_configured_mode():
    fake = FakeCapture(limit=1)
    fake.fourcc = "NV12"
    src, calls = make_source(fake, width=1280, height=720, fps=30, fourcc="NV12")
    with src:
        src.start()
        assert calls == [
            (
                0,
                {
                    "video_size": "1280x720",
                    "framerate": "30",
                    "pixel_format": "nv12",
                    "rtbufsize": str(1280 * 720 * 4 * 4),
                },
            )
        ]
        stats = src.stats()
        assert (stats.width, stats.height, stats.fourcc) == (8, 8, "NV12")


def test_compressed_or_unknown_fourcc_rejected():
    with pytest.raises(ValueError, match="fourcc"):
        DeviceSource(0, fourcc="MJPG", opener=lambda *a: FakeCapture())


def test_stats_reports_fps_over_last_second():
    fake = FakeCapture(delay=0.01)
    src, _ = make_source(fake)
    with src:
        src.start()
        time.sleep(0.3)
        stats = src.stats()
        assert not stats.stale
        assert stats.frames > 5
        assert 5 < stats.fps <= stats.frames


def test_stale_when_reads_fail():
    fake = FakeCapture()
    src, _ = make_source(fake)
    with src:
        src.start()
        assert not src.stats().stale
        fake.failing = True
        time.sleep(0.05)
        ts = src.latest().ts
        time.sleep(0.6)
        assert src.stats().stale is True
        assert src.latest().ts == ts


def test_close_stops_thread():
    fake = FakeCapture()
    src, _ = make_source(fake)
    src.start()
    assert any(t.name == "capture-device-0" for t in threading.enumerate())
    src.close()
    assert not any(t.name == "capture-device-0" for t in threading.enumerate())
    assert fake.released
    produced = fake.produced
    time.sleep(0.02)
    assert fake.produced == produced
    src.close()


def test_frame_ts_comes_from_project_clock():
    fake = FakeCapture(limit=1)
    src, _ = make_source(fake)
    before = clock.now()
    with src:
        src.start()
        frame = src.latest()
        after = clock.now()
    assert before <= frame.ts <= after


# Delivers one frame, then read() blocks until `gate` is set, like a hung driver.
class BlockingCapture(FakeCapture):
    def __init__(self):
        super().__init__()
        self.gate = threading.Event()
        self.blocked = threading.Event()

    def read(self):
        if self.produced == 0:
            return super().read()
        self.blocked.set()
        self.gate.wait()
        return False, None


def test_close_does_not_release_during_in_flight_read(monkeypatch, caplog):
    monkeypatch.setattr(device, "_JOIN_TIMEOUT_S", 0.05)
    fake = BlockingCapture()
    src, _ = make_source(fake)
    src.start()
    assert fake.blocked.wait(timeout=2)
    with caplog.at_level(logging.WARNING, logger=device.__name__):
        src.close()
    assert not fake.released
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "index 0" in warnings[0].getMessage()
    fake.gate.set()
    assert wait_until(lambda: fake.released)


def test_source_is_single_use():
    fake = FakeCapture()
    src, _ = make_source(fake)
    src.start()
    with pytest.raises(RuntimeError, match="single-use"):
        src.start()
    src.close()
    with pytest.raises(RuntimeError, match="single-use"):
        src.start()


class NoFourccCapture(FakeCapture):
    @property
    def fourcc(self):
        raise RuntimeError("driver query failed")


def test_start_releases_device_when_setup_fails_before_reader_thread():
    fake = NoFourccCapture()
    src, _ = make_source(fake)
    with pytest.raises(RuntimeError, match="driver query failed"):
        src.start()
    assert fake.released
    src.close()


DEVICE_LOG = (
    '"Integrated Webcam" (video)\n'
    '  Alternative name "@device_pnp_\\\\?\\usb#vid_0c45"\n'
    '"AVerMedia GC551 Video Capture" (audio, video)\n'
    '  Alternative name "@device_pnp_\\\\?\\usb#vid_07ca&pid_0551"\n'
    '"Microphone (USB Audio)" (audio)\n'
    "Could not enumerate audio only devices (or none found).\n"
)


def test_parse_device_list_keeps_video_devices_in_order():
    assert parse_device_list(DEVICE_LOG) == [
        "Integrated Webcam",
        "AVerMedia GC551 Video Capture",
    ]
    assert parse_device_list("") == []


# lavfi's test pattern is a synthetic source that exists on every platform.
def test_av_capture_reads_bgr_frames_from_a_real_input():
    cap = AvCapture("testsrc=size=64x48:rate=30", "lavfi", {})
    try:
        ok, first = cap.read()
        ok2, second = cap.read()
    finally:
        cap.release()
    assert ok and ok2
    assert first.shape == (48, 64, 3)
    assert first.dtype == np.uint8
    assert isinstance(cap.fourcc, str)
    assert not np.array_equal(first, second)


def test_av_capture_open_failure_propagates():
    with pytest.raises(Exception, match="no-such-filter"):
        AvCapture("no-such-filter", "lavfi", {})


def test_resolve_device_distinguishes_devices_with_the_same_name():
    names = ["Cam", "Card", "Cam", "Cam"]
    assert resolve_device(names, 0) == ("Cam", 0)
    assert resolve_device(names, 1) == ("Card", 0)
    assert resolve_device(names, 2) == ("Cam", 1)
    assert resolve_device(names, 3) == ("Cam", 2)


def test_resolve_device_rejects_an_index_out_of_range():
    with pytest.raises(CaptureError, match='index 1; found 0: "Cam"'):
        resolve_device(["Cam"], 1)
    with pytest.raises(CaptureError, match="found none"):
        resolve_device([], 0)
    with pytest.raises(CaptureError):
        resolve_device(["Cam"], -1)


def _open_missing_filter():
    with contextlib.suppress(Exception):
        av.open("no-such-filter", format="lavfi")


def test_logged_output_captures_and_restores_the_callers_log_level():
    av.logging.set_level(av.logging.WARNING)
    try:
        text = device.logged_output(_open_missing_filter)
        assert av.logging.get_level() == av.logging.WARNING
    finally:
        av.logging.set_level(None)
    assert text != ""
    assert device.logged_output(lambda: None) == ""
    assert av.logging.get_level() is None


def test_logged_output_restores_the_log_level_when_the_action_raises():
    def boom():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        device.logged_output(boom)
    assert av.logging.get_level() is None
