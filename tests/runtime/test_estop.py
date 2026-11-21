import threading
import time

import pytest

from autoplay.core.controller.base import ControllerError
from autoplay.core.controller.dummy import DummyController
from autoplay.core.controller.types import PadState
from autoplay.core.runtime.estop import EmergencyStop, EStop


def test_trip_sets_the_flag_and_runs_the_callback_once():
    calls = []
    estop = EStop(on_trip=lambda: calls.append(1), hotkey=False)
    assert not estop.tripped.is_set()
    estop.trip()
    estop.trip()
    assert estop.tripped.is_set()
    assert calls == [1]


def test_a_callback_added_after_the_trip_runs_at_once():
    estop = EStop(hotkey=False)
    estop.trip()
    calls = []
    estop.add_callback(lambda: calls.append(1))
    assert calls == [1]


def test_trip_calls_release_all_once():
    pad = DummyController()
    estop = EStop(hotkey=False)
    pad.attach_estop(estop)
    pad.hold("A")
    estop.trip()
    estop.trip()
    assert pad.release_all_calls == 1
    assert pad.state == PadState()


def test_calls_after_trip_raise_emergency_stop():
    pad = DummyController()
    estop = EStop(hotkey=False)
    pad.attach_estop(estop)
    estop.trip()
    sent = len(pad.log)
    for call in (
        lambda: pad.press("A"),
        lambda: pad.hold("A"),
        lambda: pad.release("A"),
        lambda: pad.stick("L", 1, 0),
        pad.release_all,
        pad.heartbeat,
    ):
        with pytest.raises(EmergencyStop):
            call()
    assert len(pad.log) == sent
    assert pad.release_all_calls == 1


def test_a_press_interrupted_by_the_stop_does_not_send_its_second_half():
    estop = EStop(hotkey=False)

    class TripsOnFirstApply(DummyController):
        def _apply(self, state, hold_ms):
            super()._apply(state, hold_ms)
            estop.trip()

    pad = TripsOnFirstApply()
    pad.attach_estop(estop)
    with pytest.raises(EmergencyStop):
        pad.press("A")
    assert len(pad.log) == 1
    assert pad.release_all_calls == 1


def test_a_call_cut_short_by_the_stop_raises_emergency_stop_not_a_link_error():
    estop = EStop(hotkey=False)

    class CutShort(DummyController):
        def _apply(self, state, hold_ms):
            estop.trip()
            raise ControllerError("the state call was overtaken by release_all()")

    pad = CutShort()
    pad.attach_estop(estop)
    with pytest.raises(EmergencyStop):
        pad.hold("A")


def test_a_failing_release_still_leaves_the_stop_tripped():
    class BrokenLink(DummyController):
        def _release_all(self):
            raise ControllerError("serial port failed")

    pad = BrokenLink()
    estop = EStop(hotkey=False)
    pad.attach_estop(estop)
    estop.trip()
    assert estop.tripped.is_set()
    with pytest.raises(EmergencyStop):
        pad.press("A")


def test_the_key_trips_the_stop():
    down = threading.Event()
    estop = EStop(key_down=down.is_set)
    time.sleep(0.06)
    assert not estop.tripped.is_set()
    down.set()
    assert estop.tripped.wait(0.5)
    estop.close()


def test_close_stops_watching_the_key():
    down = threading.Event()
    estop = EStop(key_down=down.is_set)
    estop.close()
    down.set()
    time.sleep(0.08)
    assert not estop.tripped.is_set()
    assert not [t for t in threading.enumerate() if t.name == "estop-key"]


def test_no_key_thread_without_a_hotkey():
    EStop(hotkey=False)
    assert not [t for t in threading.enumerate() if t.name == "estop-key"]
