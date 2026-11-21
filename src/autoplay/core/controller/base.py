from abc import ABC, abstractmethod
from dataclasses import replace
from typing import Literal

from autoplay.core.actions.timing import Timing, load_timing
from autoplay.core.controller.types import Button, Hat, PadState
from autoplay.core.runtime.estop import EmergencyStop, EStop

ButtonName = str

_DPAD_AXIS = {"DPAD_UP": "v", "DPAD_DOWN": "v", "DPAD_LEFT": "h", "DPAD_RIGHT": "h"}
_HATS = {
    (None, None): Hat.NEUTRAL,
    ("DPAD_UP", None): Hat.UP,
    ("DPAD_UP", "DPAD_RIGHT"): Hat.UP_RIGHT,
    (None, "DPAD_RIGHT"): Hat.RIGHT,
    ("DPAD_DOWN", "DPAD_RIGHT"): Hat.DOWN_RIGHT,
    ("DPAD_DOWN", None): Hat.DOWN,
    ("DPAD_DOWN", "DPAD_LEFT"): Hat.DOWN_LEFT,
    (None, "DPAD_LEFT"): Hat.LEFT,
    ("DPAD_UP", "DPAD_LEFT"): Hat.UP_LEFT,
}

BUTTON_NAMES: tuple[str, ...] = (*(b.name for b in Button), *_DPAD_AXIS)


class ControllerError(RuntimeError):
    """The gamepad could not be driven as asked."""


class ControllerTimeout(ControllerError):
    """The device did not confirm a command or a state in time."""


class UnknownButton(ValueError):
    """A button name that is not one of `BUTTON_NAMES`."""


def _stick_byte(value: float) -> int:
    if not -1.0 <= value <= 1.0:
        raise ValueError(f"stick value {value} is outside [-1, 1]")
    return round(128 + value * (127 if value >= 0 else 128))


class Controller(ABC):
    """A gamepad that is told complete states. Every call blocks until the
    device has shown what was asked for the time asked.

    Buttons are named by `BUTTON_NAMES`: the generic Switch names plus
    `DPAD_UP`, `DPAD_DOWN`, `DPAD_LEFT` and `DPAD_RIGHT`."""

    def __init__(self, timing: Timing | None = None) -> None:
        self.timing = timing or load_timing()
        self.state = PadState()
        # Held D-pad directions, oldest first: of two opposite ones the later wins.
        self._dpad: list[str] = []
        self._estop: EStop | None = None

    def attach_estop(self, estop: EStop) -> None:
        """Tie this controller to an emergency stop: when it trips, everything
        is released once, and every call from then on raises `EmergencyStop`."""
        self._estop = estop
        estop.add_callback(self._on_estop)

    def _on_estop(self) -> None:
        self._dpad.clear()
        self.state = PadState()
        try:
            self._release_all()
        except ControllerError:
            # The stop stands either way; with the link gone the device's own
            # watchdog does the releasing.
            pass

    def _check_estop(self) -> None:
        if self._estop is not None and self._estop.tripped.is_set():
            raise EmergencyStop("the emergency stop was tripped")

    def press(self, button: ButtonName, ms: int | None = None) -> None:
        """Press `button` for `ms` (default `press_ms`), then release it for
        `release_gap_ms`. Other held buttons and the sticks stay as they are."""
        ms = self.timing.press_ms if ms is None else ms
        self._check_ms(ms)
        self._set_button(button, True, ms)
        self._set_button(button, False, self.timing.release_gap_ms)

    def hold(self, button: ButtonName) -> None:
        """Press `button` and keep it pressed. It stays pressed only while the
        control loop keeps calling in (see `heartbeat`)."""
        self._set_button(button, True, self.timing.min_state_ms)

    def release(self, button: ButtonName) -> None:
        self._set_button(button, False, self.timing.min_state_ms)

    def stick(
        self, which: Literal["L", "R"], x: float, y: float, ms: int | None = None
    ) -> None:
        """Deflect a stick; x and y are in [-1, 1] and +y is up. With `ms` the
        stick is held there for `ms` and then centred for `release_gap_ms`.
        Without it the stick stays deflected, under the same condition as `hold`."""
        if which not in ("L", "R"):
            raise ValueError(f"stick must be 'L' or 'R', got {which!r}")
        if ms is not None:
            self._check_ms(ms)
        deflected = self._with_stick(which, _stick_byte(x), _stick_byte(-y))
        if ms is None:
            self._set(deflected, self.timing.min_state_ms)
            return
        self._set(deflected, ms)
        self._set(self._with_stick(which, 128, 128), self.timing.release_gap_ms)

    def release_all(self) -> None:
        """Release every button and centre both sticks at once."""
        self._check_estop()
        self._dpad.clear()
        self.state = PadState()
        self._release_all()

    def heartbeat(self) -> None:
        """Tell the controller the control loop is alive. A held button or
        stick is released by the device once these calls (or any other call)
        stop for longer than the lease."""
        self._check_estop()

    def close(self) -> None:
        """Release everything and free the device."""

    @abstractmethod
    def _apply(self, state: PadState, hold_ms: int) -> None:
        """Show `state` for `hold_ms` and return when that time has passed."""

    @abstractmethod
    def _release_all(self) -> None: ...

    def _set(self, state: PadState, hold_ms: int) -> None:
        self._check_estop()
        try:
            self._apply(state, hold_ms)
        except ControllerError as exc:
            # A stop that trips during the call cuts it short through the
            # backend; the caller should see the stop, not a link error.
            if self._estop is not None and self._estop.tripped.is_set():
                raise EmergencyStop("the emergency stop was tripped") from exc
            raise
        self._check_estop()
        self.state = state

    @staticmethod
    def _check_ms(ms: int) -> None:
        if not 0 <= ms <= 0xFFFF:
            raise ValueError(f"duration {ms} ms is outside 0..65535")

    def _set_button(self, button: ButtonName, pressed: bool, hold_ms: int) -> None:
        if button not in _DPAD_AXIS:
            try:
                bit = Button[button]
            except KeyError:
                names = ", ".join(BUTTON_NAMES)
                raise UnknownButton(
                    f"unknown button {button!r}; expected one of {names}"
                ) from None
            buttons = self.state.buttons | bit if pressed else self.state.buttons & ~bit
            self._set(replace(self.state, buttons=buttons), hold_ms)
            return
        # A direction pressed again moves to the end, so it wins from now on.
        dpad = [d for d in self._dpad if d != button]
        if pressed:
            dpad.append(button)
        latest: dict[str, str | None] = {"v": None, "h": None}
        for direction in dpad:
            latest[_DPAD_AXIS[direction]] = direction
        self._set(replace(self.state, hat=_HATS[latest["v"], latest["h"]]), hold_ms)
        # Recorded only once the device has shown it, like `state`.
        self._dpad = dpad

    def _with_stick(self, which: str, x: int, y: int) -> PadState:
        if which == "L":
            return replace(self.state, lx=x, ly=y)
        return replace(self.state, rx=x, ry=y)
