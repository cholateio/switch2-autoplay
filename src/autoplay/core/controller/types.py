import enum
from dataclasses import dataclass


class Button(enum.IntFlag):
    """Bit positions in the gamepad report; names are the generic Switch ones."""

    Y = 0x0001
    B = 0x0002
    A = 0x0004
    X = 0x0008
    L = 0x0010
    R = 0x0020
    ZL = 0x0040
    ZR = 0x0080
    MINUS = 0x0100
    PLUS = 0x0200
    LS = 0x0400
    RS = 0x0800
    HOME = 0x1000
    CAPTURE = 0x2000


class Hat(enum.IntEnum):
    """D-pad direction: clockwise from up in 45-degree steps."""

    UP = 0
    UP_RIGHT = 1
    RIGHT = 2
    DOWN_RIGHT = 3
    DOWN = 4
    DOWN_LEFT = 5
    LEFT = 6
    UP_LEFT = 7
    NEUTRAL = 8


_NO_BUTTONS = Button(0)


@dataclass(frozen=True)
class PadState:
    """One complete gamepad state. Sticks are 0-255 with 128 centred; on the
    y axes 0 is up."""

    buttons: Button = _NO_BUTTONS
    hat: Hat = Hat.NEUTRAL
    lx: int = 128
    ly: int = 128
    rx: int = 128
    ry: int = 128
