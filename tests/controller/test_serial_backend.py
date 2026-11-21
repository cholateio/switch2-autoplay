import dataclasses
import threading
import time

import pytest
from fake_device import FakeDevice

from autoplay.core import clock
from autoplay.core.actions.timing import load_timing
from autoplay.core.controller.base import ControllerError, ControllerTimeout
from autoplay.core.controller.protocol import MsgType
from autoplay.core.controller.serial_backend import SerialController
from autoplay.core.controller.types import Button, PadState

# Short link timeouts keep the failure-path tests quick; the watchdog (300 ms)
# is the device's and stays real.
FAST = dataclasses.replace(load_timing(), done_slack_ms=100, hello_window_ms=1000)


@pytest.fixture
def connect():
    opened = []

    def _connect(device: FakeDevice, **timing) -> SerialController:
        pad = SerialController(
            "fake", timing=dataclasses.replace(FAST, **timing), transport=device
        )
        opened.append(pad)
        return pad

    yield _connect
    for pad in opened:
        pad.close()


def test_open_reports_device_info(connect):
    pad = connect(FakeDevice())
    assert pad.info().proto_ver == 1
    assert pad.info().backend == 1


def test_open_survives_boot_garbage(connect):
    device = FakeDevice(
        boot_garbage=b"\x00\xff\xa5ESP-ROM:esp32s3-20210327\r\n" + bytes(range(33)),
        mute_s=0.3,
    )
    pad = connect(device)
    pad.press("A", ms=20)
    assert device.applied[0] == (PadState(buttons=Button.A), 20)


def test_open_fails_when_nothing_answers():
    device = FakeDevice(mute_s=60)
    with pytest.raises(ControllerTimeout):
        SerialController(
            "fake",
            timing=dataclasses.replace(FAST, hello_window_ms=200),
            transport=device,
        )
    assert device.closed


def test_open_rejects_another_protocol_version():
    device = FakeDevice(proto_ver=2)
    with pytest.raises(ControllerError, match="protocol version 2"):
        SerialController("fake", timing=FAST, transport=device)
    assert device.closed


def test_press_blocks_until_done(connect):
    device = FakeDevice()
    pad = connect(device)
    start = clock.now()
    pad.press("A")
    elapsed = clock.now() - start
    assert device.applied == [(PadState(buttons=Button.A), 80), (PadState(), 50)]
    assert elapsed >= 0.130
    assert device.state == PadState()


def test_retransmits_on_lost_ack_without_double_apply(connect):
    device = FakeDevice(drop_acks=1)
    pad = connect(device)
    # Longer than the ACK timeout, so the missing ACK is noticed before DONE could stand in for it.
    pad.press("A", ms=200)
    assert device.applied == [(PadState(buttons=Button.A), 200), (PadState(), 50)]
    pressed_seq = device.seqs[MsgType.SET_STATE][0]
    assert device.seqs[MsgType.SET_STATE].count(pressed_seq) == 2


def test_done_stands_in_for_a_lost_ack(connect):
    device = FakeDevice(drop_acks=1)
    pad = connect(device)
    pad.hold("A")
    assert len(device.seqs[MsgType.SET_STATE]) == 1
    assert pad.state == PadState(buttons=Button.A)


def test_raises_after_ack_retries_exhausted(connect):
    device = FakeDevice()
    pad = connect(device)
    device.deaf = True
    before = device.writes
    with pytest.raises(ControllerTimeout):
        pad.hold("A")
    assert device.writes - before == 1 + FAST.ack_retries
    # What the device shows is unknown now, so the controller's own view is unchanged.
    assert pad.state == PadState()


def test_busy_waits_then_retries(connect):
    device = FakeDevice(busy_first=2)
    pad = connect(device)
    pad.hold("A")
    assert len(device.applied) == 1
    assert len(device.seqs[MsgType.SET_STATE]) == 3


def test_busy_forever_raises(connect):
    device = FakeDevice(busy_first=10_000)
    pad = connect(device)
    with pytest.raises(ControllerTimeout):
        pad.hold("A")


def test_lost_done_recovered_by_same_seq_query(connect):
    device = FakeDevice(drop_dones=1)
    pad = connect(device)
    pad.press("A", ms=20)
    assert device.applied == [(PadState(buttons=Button.A), 20), (PadState(), 50)]
    # The pressed state was sent once and asked about once, under one seq.
    pressed_seq = device.seqs[MsgType.SET_STATE][0]
    assert device.seqs[MsgType.SET_STATE].count(pressed_seq) == 2


def test_dropped_seq_raises_controller_timeout(connect):
    device = FakeDevice()
    pad = connect(device)
    threading.Timer(0.1, device.trip_watchdog).start()
    with pytest.raises(ControllerTimeout, match="dropped"):
        pad.stick("L", 0, 1, ms=400)
    assert device.state == PadState()


def test_state_that_never_reaches_the_console_times_out(connect):
    device = FakeDevice(mounted=False)
    pad = connect(device, done_queries=1)
    with pytest.raises(ControllerTimeout, match="USB"):
        pad.hold("A")


def test_heartbeat_keeps_watchdog_fed_during_blocking_hold(connect):
    device = FakeDevice()
    pad = connect(device, lease_ms=200)
    pad.stick("L", 0, 1, ms=1200)
    assert device.wd_trips == 0
    assert device.pings >= 8


def test_heartbeat_stops_when_lease_expires(connect):
    device = FakeDevice()
    pad = connect(device, lease_ms=300)
    pad.hold("A")
    time.sleep(0.8)
    assert device.wd_trips == 1
    assert device.state == PadState()


def test_explicit_heartbeat_renews_lease(connect):
    device = FakeDevice()
    pad = connect(device, lease_ms=300)
    pad.hold("A")
    for _ in range(10):
        time.sleep(0.1)
        pad.heartbeat()
    assert device.wd_trips == 0
    assert device.state == PadState(buttons=Button.A)


def test_heartbeat_thread_is_daemon(connect):
    connect(FakeDevice())
    threads = [t for t in threading.enumerate() if t.name.startswith("pad-")]
    assert len(threads) == 2
    assert all(t.daemon for t in threads)


def test_close_stops_heartbeat():
    device = FakeDevice()
    pad = SerialController("fake", timing=FAST, transport=device)
    pad.hold("A")
    pad.close()
    assert device.releases == 1
    assert device.closed
    assert not [t for t in threading.enumerate() if t.name.startswith("pad-")]
    pings = device.pings
    time.sleep(0.45)
    assert device.pings == pings
    assert device.wd_trips == 1


def test_calls_after_close_raise(connect):
    pad = connect(FakeDevice())
    pad.close()
    with pytest.raises(ControllerError, match="closed"):
        pad.press("A")
    with pytest.raises(ControllerError, match="closed"):
        pad.heartbeat()
    pad.close()


def test_release_all_reaches_the_device(connect):
    device = FakeDevice()
    pad = connect(device)
    pad.hold("A")
    pad.release_all()
    assert device.releases == 1
    assert device.state == PadState()
    assert pad.state == PadState()


def test_state_seqs_and_other_seqs_never_overlap(connect):
    device = FakeDevice()
    pad = connect(device, heartbeat_ms=5)
    for _ in range(140):
        pad.hold("A")
        pad.heartbeat()
    states = device.seqs[MsgType.SET_STATE]
    others = device.seqs[MsgType.PING] + device.seqs[MsgType.HELLO]
    assert max(states) <= 127
    assert min(others) >= 128
    # Wrapped at least once, and every one of the 140 states was really applied.
    assert len(set(states)) == 128
    assert len(device.applied) == 140


def test_a_failed_port_surfaces_as_controller_error(connect):
    device = FakeDevice()
    pad = connect(device)
    device.closed = True
    with pytest.raises(ControllerError):
        pad.hold("A")


def test_a_port_that_does_not_exist_is_a_controller_error():
    with pytest.raises(ControllerError, match="cannot open serial port"):
        SerialController("no-such-port-for-this-test", timing=FAST)
