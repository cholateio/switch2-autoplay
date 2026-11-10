import logging
import threading
import time

import cv2
import numpy as np
import pytest

from autoplay.core import clock
from autoplay.core.capture import device
from autoplay.core.capture.base import CaptureError
from autoplay.core.capture.device import DeviceSource


# Stands in for cv2.VideoCapture; frame n is an 8x8 image filled with n % 256.
class FakeCapture:
    def __init__(self, *, opened=True, limit=None, delay=0.001):
        self.opened = opened
        self.limit = limit
        self.delay = delay
        self.failing = False
        self.produced = 0
        self.released = False
        self.props = {}

    def isOpened(self):
        return self.opened

    def set(self, prop, value):
        self.props[prop] = value
        return True

    def get(self, prop):
        return self.props.get(prop, 0.0)

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
    fake = FakeCapture(opened=False)
    src, _ = make_source(fake)
    with pytest.raises(CaptureError) as exc:
        src.start()
    assert "index 0" in str(exc.value)
    assert "dshow" in str(exc.value)
    assert fake.released


def test_start_raises_when_no_first_frame(monkeypatch):
    monkeypatch.setattr(device, "_FIRST_FRAME_TIMEOUT_S", 0.1)
    fake = FakeCapture(limit=0)
    src, _ = make_source(fake, backend="msmf")
    with pytest.raises(CaptureError) as exc:
        src.start()
    assert "index 0" in str(exc.value)
    assert "msmf" in str(exc.value)
    assert fake.released


def test_start_configures_device():
    fake = FakeCapture(limit=1)
    src, calls = make_source(fake, width=1280, height=720, fps=30, fourcc="NV12")
    with src:
        src.start()
        assert calls == [(0, cv2.CAP_DSHOW)]
        assert fake.props[cv2.CAP_PROP_BUFFERSIZE] == 1
        assert fake.props[cv2.CAP_PROP_FOURCC] == cv2.VideoWriter.fourcc(*"NV12")
        assert fake.props[cv2.CAP_PROP_FRAME_WIDTH] == 1280
        assert fake.props[cv2.CAP_PROP_FRAME_HEIGHT] == 720
        assert fake.props[cv2.CAP_PROP_FPS] == 30
        stats = src.stats()
        assert (stats.width, stats.height, stats.fourcc) == (8, 8, "NV12")


def test_unknown_backend_rejected():
    with pytest.raises(ValueError, match="backend"):
        DeviceSource(0, backend="v4l2", opener=lambda *a: FakeCapture())


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

    def read(self):
        if self.produced == 0:
            return super().read()
        self.gate.wait()
        return False, None


def test_close_does_not_release_during_in_flight_read(monkeypatch, caplog):
    monkeypatch.setattr(device, "_JOIN_TIMEOUT_S", 0.05)
    fake = BlockingCapture()
    src, _ = make_source(fake)
    src.start()
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
