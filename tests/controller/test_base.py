import pytest

from autoplay.core.controller.base import BUTTON_NAMES, UnknownButton
from autoplay.core.controller.dummy import DummyController
from autoplay.core.controller.types import Button, Hat, PadState


def test_press_emits_pressed_then_released():
    pad = DummyController()
    pad.press("A")
    assert pad.log == [(PadState(buttons=Button.A), 80), (PadState(), 50)]
    assert pad.state == PadState()


def test_press_takes_a_duration():
    pad = DummyController()
    pad.press("B", ms=0)
    assert pad.log == [(PadState(buttons=Button.B), 0), (PadState(), 50)]


def test_press_keeps_other_held_buttons():
    pad = DummyController()
    pad.hold("ZL")
    pad.press("A")
    assert [state.buttons for state, _ in pad.log] == [
        Button.ZL,
        Button.ZL | Button.A,
        Button.ZL,
    ]


def test_hold_and_release():
    pad = DummyController()
    pad.hold("X")
    assert pad.state.buttons == Button.X
    pad.release("X")
    assert pad.state == PadState()
    assert [hold for _, hold in pad.log] == [16, 16]


def test_every_button_name_is_accepted():
    pad = DummyController()
    for name in BUTTON_NAMES:
        pad.hold(name)
        pad.release(name)
    assert pad.state == PadState()
    assert len(BUTTON_NAMES) == 18


@pytest.mark.parametrize(
    "held, hat",
    [
        (["DPAD_UP"], Hat.UP),
        (["DPAD_UP", "DPAD_RIGHT"], Hat.UP_RIGHT),
        (["DPAD_RIGHT"], Hat.RIGHT),
        (["DPAD_RIGHT", "DPAD_DOWN"], Hat.DOWN_RIGHT),
        (["DPAD_DOWN"], Hat.DOWN),
        (["DPAD_DOWN", "DPAD_LEFT"], Hat.DOWN_LEFT),
        (["DPAD_LEFT"], Hat.LEFT),
        (["DPAD_LEFT", "DPAD_UP"], Hat.UP_LEFT),
        (["DPAD_LEFT", "DPAD_RIGHT"], Hat.RIGHT),
        (["DPAD_RIGHT", "DPAD_LEFT"], Hat.LEFT),
        (["DPAD_UP", "DPAD_DOWN", "DPAD_RIGHT"], Hat.DOWN_RIGHT),
    ],
)
def test_dpad_combines_into_hat(held, hat):
    pad = DummyController()
    for name in held:
        pad.hold(name)
    assert pad.state.hat == hat
    assert pad.state.buttons == Button(0)


def test_releasing_the_later_direction_restores_the_earlier_one():
    pad = DummyController()
    pad.hold("DPAD_LEFT")
    pad.hold("DPAD_RIGHT")
    pad.release("DPAD_RIGHT")
    assert pad.state.hat == Hat.LEFT
    pad.release("DPAD_LEFT")
    assert pad.state.hat == Hat.NEUTRAL


def test_stick_maps_unit_to_bytes():
    pad = DummyController()
    pad.stick("L", 0, 1)
    assert (pad.state.lx, pad.state.ly) == (128, 0)
    pad.stick("L", 1, 0)
    assert (pad.state.lx, pad.state.ly) == (255, 128)
    pad.stick("L", -1, -1)
    assert (pad.state.lx, pad.state.ly) == (0, 255)
    pad.stick("L", 0, 0)
    assert (pad.state.lx, pad.state.ly) == (128, 128)
    pad.stick("R", 0.5, -0.5)
    assert (pad.state.rx, pad.state.ry) == (192, 192)


def test_timed_stick_recenters_that_stick_only():
    pad = DummyController()
    pad.hold("ZL")
    pad.hold("DPAD_UP")
    pad.stick("R", 1, 0)
    before = len(pad.log)

    pad.stick("L", 0, 1, ms=500)

    (deflected, hold), (centred, gap) = pad.log[before:]
    assert (deflected.lx, deflected.ly, hold) == (128, 0, 500)
    assert (centred.lx, centred.ly, gap) == (128, 128, 50)
    for state in (deflected, centred):
        assert (state.buttons, state.hat, state.rx, state.ry) == (
            Button.ZL,
            Hat.UP,
            255,
            128,
        )


def test_untimed_stick_stays_deflected():
    pad = DummyController()
    pad.stick("L", 0, 1)
    assert len(pad.log) == 1
    assert pad.state.ly == 0


def test_unknown_button_raises():
    pad = DummyController()
    with pytest.raises(UnknownButton):
        pad.press("TURBO")
    assert pad.log == []


def test_bad_arguments_change_nothing():
    pad = DummyController()
    with pytest.raises(ValueError):
        pad.stick("L", 1.5, 0)
    with pytest.raises(ValueError):
        pad.stick("M", 0, 0)
    with pytest.raises(ValueError):
        pad.press("A", ms=-1)
    with pytest.raises(ValueError):
        pad.press("A", ms=70000)
    assert pad.log == []
    assert pad.state == PadState()


def test_release_all_resets_state():
    pad = DummyController()
    pad.hold("A")
    pad.hold("DPAD_LEFT")
    pad.stick("L", 1, 1)
    pad.release_all()
    assert pad.state == PadState()
    assert pad.release_all_calls == 1
    # The D-pad memory is cleared too: an old direction must not come back.
    pad.hold("DPAD_UP")
    assert pad.state.hat == Hat.UP
