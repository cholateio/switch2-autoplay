import dataclasses
import threading
import time

import pytest
from fake_device import FakeDevice

from autoplay.core import clock
from autoplay.core.actions.timing import load_timing
from autoplay.core.controller.base import ControllerError, ControllerTimeout
from autoplay.core.controller.protocol import Msg, MsgType, pack_set_state
from autoplay.core.controller.serial_backend import SerialController
from autoplay.core.controller.types import Button, PadState
from autoplay.core.runtime.estop import EmergencyStop, EStop

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


def test_after_a_failed_state_nothing_is_sent_until_release_all(connect):
    device = FakeDevice()
    pad = connect(device)
    pad.hold("A")
    device.deaf = True
    with pytest.raises(ControllerTimeout):
        pad.hold("B")
    writes = device.writes

    with pytest.raises(ControllerError, match="release_all"):
        pad.hold("B")
    assert device.writes == writes

    device.deaf = False
    pad.release_all()
    # The resynchronisation is a new session: the device forgets every seq.
    assert len(device.seqs[MsgType.HELLO]) == 2
    assert device.state == PadState()
    pad.hold("B")
    assert device.state == PadState(buttons=Button.B)


def test_failed_states_cannot_wrap_seq_onto_a_remembered_one(connect):
    device = FakeDevice()
    pad = connect(device, ack_timeout_ms=1, ack_retries=1, hello_window_ms=20)
    pad.hold("A")
    assert device.seqs[MsgType.SET_STATE] == [0]

    # A long outage in which the caller keeps retrying, as an action loop would.
    device.deaf = True
    # 127 failures use up seq 1..127, so the next state would get seq 0 again.
    for _ in range(127):
        with pytest.raises(ControllerError):
            pad.hold("B")
        with pytest.raises(ControllerError):
            pad.release_all()
    device.deaf = False

    pad.release_all()
    pad.hold("B")
    # The device really showed B; a wrapped seq would have been answered DONE unseen.
    assert device.applied[-1][0] == PadState(buttons=Button.B)
    assert device.state == PadState(buttons=Button.B)


def test_release_all_failure_also_requires_a_resync(connect):
    device = FakeDevice()
    pad = connect(device)
    pad.hold("A")
    device.deaf = True
    with pytest.raises(ControllerTimeout):
        pad.release_all()
    device.deaf = False
    with pytest.raises(ControllerError, match="release_all"):
        pad.hold("B")
    pad.release_all()
    assert device.state == PadState()


def run_in_thread(call):
    """Run `call` on a thread; returns (thread, list that receives its exception)."""
    raised = []

    def target():
        try:
            call()
        except Exception as exc:  # noqa: BLE001 - handed to the test
            raised.append(exc)

    thread = threading.Thread(target=target)
    thread.start()
    return thread, raised


def test_release_all_from_another_thread_ends_a_long_hold(connect):
    device = FakeDevice()
    pad = connect(device)
    thread, raised = run_in_thread(lambda: pad.stick("L", 0, 1, ms=5000))
    time.sleep(0.15)
    assert device.state.ly == 0

    pad.release_all()
    thread.join(0.5)

    assert not thread.is_alive()
    assert isinstance(raised[0], ControllerError)
    assert device.state == PadState()
    # The release settled the device, so the next call needs no new session.
    pad.hold("B")
    assert device.state == PadState(buttons=Button.B)
    assert len(device.seqs[MsgType.HELLO]) == 1


def test_a_waiting_state_is_not_shown_again_after_a_new_session(connect):
    device = FakeDevice(drop_dones=1)
    pad = connect(device, done_slack_ms=150)
    thread, raised = run_in_thread(lambda: pad.stick("L", 0, 1, ms=30))
    # The state has finished on the device, its DONE was lost, and the caller
    # is still waiting before it asks again with the same seq.
    time.sleep(0.1)
    pad._desynced = True
    pad.release_all()
    thread.join(1.0)

    assert not thread.is_alive()
    assert isinstance(raised[0], ControllerError)
    # HELLO made the device forget that seq; asking again would have shown the
    # stick deflection a second time.
    assert len(device.applied) == 1
    assert device.state == PadState()


def test_a_call_admitted_before_a_failure_does_not_transmit(connect):
    device = FakeDevice()
    pad = connect(device)
    generation = pad._generation
    device.deaf = True
    with pytest.raises(ControllerTimeout):
        pad.release_all()
    device.deaf = False
    # Counted per type: the heartbeat's PINGs go on meanwhile.
    states_before = list(device.seqs.get(MsgType.SET_STATE, []))

    stale = Msg(MsgType.SET_STATE, 5, pack_set_state(PadState(buttons=Button.A), 16))
    with pytest.raises(ControllerError):
        pad._request(stale, (MsgType.ACK,), generation=generation)
    assert device.seqs.get(MsgType.SET_STATE, []) == states_before


def test_close_stops_everything_even_if_the_port_fails_during_recovery():
    device = FakeDevice()
    pad = SerialController("fake", timing=FAST, transport=device)
    pad.hold("A")
    device.deaf = True
    with pytest.raises(ControllerTimeout):
        pad.hold("B")

    def broken_reset():
        raise OSError("device unplugged")

    device.reset_input = broken_reset
    pad.close()

    assert device.closed
    assert not [t for t in threading.enumerate() if t.name.startswith("pad-")]


class GatedDevice(FakeDevice):
    """Blocks inside the write of a RELEASE_ALL frame until `gate` is set."""

    def __init__(self):
        super().__init__()
        self.gate = threading.Event()
        self.release_started = threading.Event()

    def write(self, data: bytes) -> None:
        if len(data) > 2 and data[2] == MsgType.RELEASE_ALL and not self.gate.is_set():
            self.release_started.set()
            self.gate.wait(5)
        super().write(data)


def test_no_state_is_written_once_a_release_has_started(connect):
    device = GatedDevice()
    pad = connect(device)
    pad.hold("A")

    release, release_raised = run_in_thread(pad.release_all)
    assert device.release_started.wait(1)
    # The release is mid-flight; a state call made now must not get in front of it.
    state, state_raised = run_in_thread(lambda: pad.hold("B"))
    time.sleep(0.1)
    assert device.written_types.count(MsgType.SET_STATE) == 1

    device.gate.set()
    release.join(1)
    state.join(1)
    assert release_raised == []
    # The call that began after the release runs after it, on a released device.
    assert state_raised == []
    assert device.applied[-1][0] == PadState(buttons=Button.B)


def test_no_state_is_written_when_the_release_times_out(connect):
    device = GatedDevice()
    pad = connect(device)
    pad.hold("A")

    release, release_raised = run_in_thread(pad.release_all)
    assert device.release_started.wait(1)
    state, state_raised = run_in_thread(lambda: pad.hold("B"))
    time.sleep(0.05)
    device.deaf = True
    device.gate.set()
    release.join(2)
    state.join(2)

    assert isinstance(release_raised[0], ControllerTimeout)
    assert isinstance(state_raised[0], ControllerError)
    # Only the first hold("A") was ever written: the failed release left the
    # device unknown, and nothing may be shown on top of that.
    assert device.written_types.count(MsgType.SET_STATE) == 1


def test_emergency_stop_releases_and_ends_a_long_hold(connect):
    device = FakeDevice()
    pad = connect(device)
    estop = EStop(hotkey=False)
    pad.attach_estop(estop)
    thread, raised = run_in_thread(lambda: pad.stick("L", 0, 1, ms=5000))
    time.sleep(0.15)

    estop.trip()
    thread.join(0.5)

    assert not thread.is_alive()
    assert isinstance(raised[0], EmergencyStop)
    assert device.state == PadState()
    assert device.releases == 1
    with pytest.raises(EmergencyStop):
        pad.hold("A")
    pad.close()
    assert device.closed
    assert device.releases == 1


def beat_until_error(pad, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        pad.heartbeat()
        time.sleep(0.02)


def test_heartbeat_reports_a_device_that_stopped_answering(connect):
    device = FakeDevice()
    pad = connect(device)
    pad.hold("A")
    device.deaf = True
    with pytest.raises(ControllerTimeout, match="heartbeat"):
        beat_until_error(pad, 1.0)
    # The held state is gone on the device, so the controller wants a new session.
    states = len(device.seqs[MsgType.SET_STATE])
    with pytest.raises(ControllerError):
        pad.hold("B")
    assert len(device.seqs[MsgType.SET_STATE]) == states

    device.deaf = False
    pad.release_all()
    pad.heartbeat()
    pad.hold("B")
    assert device.state == PadState(buttons=Button.B)


def test_heartbeat_reports_a_port_that_went_away(connect):
    device = FakeDevice()
    pad = connect(device)
    pad.hold("A")
    device.closed = True
    with pytest.raises(ControllerError, match="serial port"):
        beat_until_error(pad, 1.0)


def test_one_lost_heartbeat_reply_is_not_an_error(connect):
    device = FakeDevice()
    pad = connect(device)
    pad.hold("A")
    device.deaf = True
    time.sleep(0.12)
    device.deaf = False
    beat_until_error(pad, 0.5)
    assert device.state == PadState(buttons=Button.A)


def test_heartbeats_stop_when_only_the_replies_are_lost(connect):
    device = FakeDevice()
    pad = connect(device)
    pad.hold("A")
    # Our frames still reach the device and would keep its watchdog fed.
    device.mute_replies = True
    with pytest.raises(ControllerTimeout, match="heartbeat"):
        beat_until_error(pad, 1.0)
    pings = device.pings
    time.sleep(0.45)
    assert device.pings == pings
    assert device.wd_trips == 1
    assert device.state == PadState()


def test_an_outage_between_two_state_calls_is_not_papered_over(connect):
    device = FakeDevice()
    pad = connect(device)
    pad.hold("A")
    # Long enough for the device's watchdog to release A; no heartbeat() call
    # is made, as in a loop that only sends states.
    device.deaf = True
    time.sleep(0.6)
    device.deaf = False
    assert device.state == PadState()

    with pytest.raises(ControllerError):
        pad.press("B")
    # Sending B on top of the remembered A would have pressed A again.
    assert all(state.buttons != Button.A | Button.B for state, _ in device.applied)
    pad.release_all()
    pad.press("B", ms=20)
    assert device.applied[-2][0] == PadState(buttons=Button.B)


def test_a_state_admitted_after_the_stop_is_not_sent(connect):
    device = FakeDevice()
    pad = connect(device)
    estop = EStop(hotkey=False)
    pad.attach_estop(estop)
    estop.trip()
    sent = len(device.seqs.get(MsgType.SET_STATE, []))
    # Past the base class's own check, as a call racing the trip would be.
    with pytest.raises(EmergencyStop):
        pad._apply(PadState(buttons=Button.A), 16)
    assert len(device.seqs.get(MsgType.SET_STATE, [])) == sent
