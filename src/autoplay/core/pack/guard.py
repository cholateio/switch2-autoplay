from typing import Literal

from autoplay.core.capture.base import CaptureError, FrameSource
from autoplay.core.controller.base import BUTTON_NAMES, Controller, UnknownButton
from autoplay.core.pack.loader import GamePack
from autoplay.core.runtime.runlog import RunLog

FRAME_TIMEOUT_S = 1.0

SENT = "sent"
REFUSED = "refused"
FAILED = "failed"


class ForbiddenInput(RuntimeError):
    """A button was refused because the pack forbids it in the current context."""

    def __init__(self, button: str, context: str | None) -> None:
        where = f"context {context!r}" if context else "an unrecognised picture"
        super().__init__(f"button {button} is forbidden in {where}")
        self.button = button
        self.context = context


class InputGuard:
    """The only way a run reaches the gamepad: every input is checked against
    the buttons the pack forbids in the context on screen, and logged.

    The context is read from two frames captured after the call began; they
    must name the same single context, otherwise the picture counts as
    unrecognised and every button forbidden anywhere is refused.

    What this does not cover: between the second frame and the device acting
    lie some tens of milliseconds. A context the game enters by itself in that
    window is not seen. The caller is one thread that waits for the picture to
    settle after each input, so the context otherwise changes only through
    inputs that went through here."""

    def __init__(
        self,
        pack: GamePack,
        source: FrameSource,
        pad: Controller,
        log: RunLog | None = None,
    ) -> None:
        self._pack = pack
        self._source = source
        self._pad = pad
        self._log = log

    def _observe(self) -> tuple[str | None, int]:
        latest = self._source.latest()
        seq = latest.seq if latest is not None else 0
        seen: list[str | None] = []
        for _ in range(2):
            frame = self._source.wait_next(seq, FRAME_TIMEOUT_S)
            if frame is None:
                raise CaptureError(
                    f"no new frame within {FRAME_TIMEOUT_S} s; the context is unknown"
                )
            seq = frame.seq
            names = self._pack.states.matches(frame.bgr)
            seen.append(names[0] if len(names) == 1 else None)
        return (seen[0] if seen[0] == seen[1] else None), seq

    def context(self) -> str | None:
        """Return the context on screen now, or None when it is not recognised.
        Raises CaptureError when the source delivers no new frame."""
        return self._observe()[0]

    def _event(self, outcome: str, **fields) -> None:
        if self._log is not None:
            self._log.event("input", outcome=outcome, **fields)

    def press(self, button: str, ms: int | None = None) -> str | None:
        """Press `button` unless the context forbids it, and return the context
        it was pressed in. Raises ForbiddenInput, with nothing sent to the
        gamepad, for a forbidden button; UnknownButton for a name that does not
        exist; CaptureError when the context cannot be read. Whatever the
        gamepad raises propagates."""
        if button not in BUTTON_NAMES:
            raise UnknownButton(
                f"unknown button {button!r}; expected one of {', '.join(BUTTON_NAMES)}"
            )
        context, seq = self._observe()
        fields = {"button": button, "context": context, "seq": seq, "ms": ms}
        if button in self._pack.forbidden_in(context):
            self._event(REFUSED, **fields)
            raise ForbiddenInput(button, context)
        self._send(lambda: self._pad.press(button, ms), fields)
        return context

    def hold_stick(
        self, which: Literal["L", "R"], x: float, y: float, ms: int
    ) -> str | None:
        """Deflect a stick for `ms` and return the context it was moved in.
        Sticks are never forbidden; the move is still logged."""
        context, seq = self._observe()
        fields = {
            "button": f"{which}_STICK",
            "context": context,
            "seq": seq,
            "ms": ms,
            "x": x,
            "y": y,
        }
        self._send(lambda: self._pad.stick(which, x, y, ms), fields)
        return context

    def _send(self, action, fields: dict) -> None:
        try:
            action()
        except Exception as exc:
            # Part of the input may have reached the device.
            self._event(FAILED, error=type(exc).__name__, **fields)
            raise
        # The gamepad call returns once the device has shown the state.
        self._event(SENT, **fields)
