import dataclasses
import time

import fake_device
import pytest
from fake_device import FakeDevice

from autoplay.core.actions.timing import load_timing
from autoplay.core.controller.serial_backend import SerialController
from autoplay.core.controller.types import Button, Hat, PadState
from autoplay.core.tools import pad_selftest
from autoplay.core.tools.pad_selftest import (
    ReportLog,
    Selftest,
    decode_report,
    first_time,
    shown_ms,
)


def test_decode_report():
    data = bytes.fromhex("04 10 02 00 ff 80 7f 00")
    assert decode_report(data) == PadState(
        Button.A | Button.HOME, Hat.RIGHT, 0, 255, 128, 127
    )
    with pytest.raises(ValueError):
        decode_report(data[:7])


def rows(*pairs):
    return [
        (t / 1000, PadState(buttons=Button.A) if on else PadState()) for t, on in pairs
    ]


def test_shown_ms_measures_the_first_run():
    def pressed(s):
        return bool(s.buttons & Button.A)

    assert shown_ms(
        rows((0, 0), (4, 1), (8, 1), (20, 1), (24, 0), (28, 1), (60, 0)), pressed
    ) == pytest.approx(20)
    assert shown_ms(rows((0, 0), (4, 0)), pressed) is None
    # A run that has not ended yet has no measured length.
    assert shown_ms(rows((0, 0), (4, 1), (8, 1)), pressed) is None


def test_first_time():
    assert first_time(rows((0, 0), (4, 1)), lambda s: bool(s.buttons)) == pytest.approx(
        0.004
    )
    assert first_time(rows((0, 0)), lambda s: bool(s.buttons)) is None


class FakeHid:
    """The USB side of a FakeDevice: one report every 4 ms with its state."""

    def __init__(self, device, corrupt=None):
        self.device = device
        self.corrupt = corrupt
        self.closed = False

    def read(self, timeout_ms):
        time.sleep(0.004)
        s = self.device.state
        data = bytearray(
            int(s.buttons).to_bytes(2, "little")
            + bytes([s.hat, s.lx, s.ly, s.rx, s.ry, 0])
        )
        if self.corrupt:
            self.corrupt(data)
        return bytes(data)

    def close(self):
        self.closed = True


def selftest_on(device, hid):
    lines = []

    def open_pad(timing):
        # The selftest opens the port twice; a real port can be reopened.
        device.closed = False
        return SerialController(
            "fake",
            timing=dataclasses.replace(timing, done_slack_ms=100),
            transport=device,
        )

    test = Selftest(open_pad, lambda backend: hid, out=lines.append)
    return test.run(), lines


def test_selftest_passes_on_a_correct_device():
    device = FakeDevice()
    hid = FakeHid(device)
    failures, lines = selftest_on(device, hid)
    assert failures == 0, lines
    assert len(lines) == 14 + 8 + 3
    assert all(line.startswith("PASS") for line in lines)
    assert hid.closed
    assert device.closed


def test_selftest_catches_a_wrong_button_bit():
    def swap_a_and_b(data):
        a, b = bool(data[0] & Button.A), bool(data[0] & Button.B)
        data[0] = (
            (data[0] & ~(Button.A | Button.B))
            | (Button.B if a else 0)
            | (Button.A if b else 0)
        )

    device = FakeDevice()
    failures, lines = selftest_on(device, FakeHid(device, corrupt=swap_a_and_b))
    failed = [line for line in lines if line.startswith("FAIL")]
    assert failures == len(failed) >= 2
    assert any(line.startswith("FAIL  A") for line in failed)
    assert any(line.startswith("FAIL  B") for line in failed)


def test_selftest_catches_a_device_that_never_releases(monkeypatch):
    # A watchdog far too slow: the stick must still be deflected when the check looks.
    monkeypatch.setattr(fake_device, "WATCHDOG_S", 5.0)
    device = FakeDevice()
    failures, lines = selftest_on(device, FakeHid(device))
    assert failures == 1
    assert lines[-1].startswith("FAIL  stick released")
    device._wd_armed = False


def test_report_log_keeps_arrival_times():
    device = FakeDevice()
    log = ReportLog(FakeHid(device))
    start = time.perf_counter()
    time.sleep(0.05)
    log.close()
    got = log.since(start)
    assert len(got) >= 5
    assert got == sorted(got, key=lambda row: row[0])
    assert log.latest() == PadState()
    device.close()


def test_timing_used_by_selftest_is_the_packaged_one():
    assert pad_selftest.WATCHDOG_MS == 300
    assert load_timing().min_state_ms == 16
