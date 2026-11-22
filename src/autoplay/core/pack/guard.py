import contextlib
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


class NeedsConfirmation(ForbiddenInput):
    """A button was refused because the pack wants it confirmed here."""

    def __init__(self, button: str, where: str) -> None:
        RuntimeError.__init__(
            self,
            f"button {button} needs a confirmation while {where!r} is on screen: "
            "send it again with `confirm` set to the option you read and mean",
        )
        self.button = button
        self.context = where


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
        self._overlays: frozenset[str] = frozenset()

    def _observe(self) -> tuple[str | None, int]:
        latest = self._source.latest()
        seq = latest.seq if latest is not None else 0
        seen: list[str | None] = []
        overlays: set[str] = set()
        for _ in range(2):
            frame = self._source.wait_next(seq, FRAME_TIMEOUT_S)
            if frame is None:
                raise CaptureError(
                    f"no new frame within {FRAME_TIMEOUT_S} s; the context is unknown"
                )
            seq = frame.seq
            names = self._pack.states.matches(frame.bgr)
            seen.append(names[0] if len(names) == 1 else None)
            # Shown in either frame counts: asking once too often is the safe side.
            overlays.update(self._pack.overlays.matches(frame.bgr))
        self._overlays = frozenset(overlays)
        return (seen[0] if seen[0] == seen[1] else None), seq

    def _observe_after(self, ms: int) -> str | None:
        # Measured on frame times, which is also what a recorded source has.
        latest = self._source.latest()
        while True:
            context, _ = self._observe()
            now = self._source.latest()
            if latest is None or now is None or (now.ts - latest.ts) * 1000 >= ms:
                return context
            self._pad.heartbeat()

    def context(self) -> str | None:
        """Return the context on screen now, or None when it is not recognised.
        Raises CaptureError when the source delivers no new frame."""
        return self._observe()[0]

    def _event(self, outcome: str, **fields) -> None:
        if self._log is not None:
            self._log.event("input", outcome=outcome, **fields)

    def _confirm_where(self, button: str, context: str | None) -> str | None:
        """The context or overlay on screen that wants `button` confirmed."""
        for where in (context, *sorted(self._overlays)):
            if where is not None and button in self._pack.confirm.get(where, ()):
                return where
        return None

    def press(
        self, button: str, ms: int | None = None, confirm: str | None = None
    ) -> str | None:
        """Press `button` unless the context forbids it, and return the context
        it was pressed in. Where the pack wants the button confirmed it is sent
        only with a non-empty `confirm`, which is logged; otherwise
        NeedsConfirmation is raised and nothing is sent. Raises ForbiddenInput, with nothing sent to the
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
        where = self._confirm_where(button, context)
        if where is not None:
            if not confirm:
                self._event(REFUSED, needs_confirm=where, **fields)
                raise NeedsConfirmation(button, where)
            fields = {**fields, "confirm": confirm, "confirm_where": where}
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

    def combo(
        self,
        held: str,
        ms: int,
        button: str | None = None,
        stick: tuple[Literal["L", "R"], float, float] | None = None,
    ) -> str | None:
        """Hold the button `held`, then press `button` or deflect `stick`
        (which, x, y) for `ms`, then release; return the context it started
        in. Exactly one of `button` and `stick` is given. Both buttons are
        checked twice: before anything is sent, and again once `held` is down,
        against the picture it produced. Forbidden before: nothing is sent.
        Forbidden after: `held` is released and the second input is not sent.
        Whenever it does not finish, everything on the gamepad is released.
        Raises as `press` does, and ValueError for a malformed request."""
        names = [held, *([button] if button is not None else [])]
        if (button is None) == (stick is None):
            raise ValueError("a combo adds exactly one action to the held button")
        if button == held:
            raise ValueError(f"{held} cannot be both held and pressed")
        for name in names:
            if name not in BUTTON_NAMES:
                raise UnknownButton(
                    f"unknown button {name!r}; expected one of {', '.join(BUTTON_NAMES)}"
                )
        context, seq = self._observe()
        fields = {
            "button": button if button is not None else f"{stick[0]}_STICK",
            "held": held,
            "context": context,
            "seq": seq,
            "ms": ms,
        }
        blocked = self._first_forbidden(names, context)
        if blocked is not None:
            self._event(REFUSED, reached=[], **fields)
            raise ForbiddenInput(blocked, context)

        # The buttons that went down or may have, for the audit: a combo is
        # several gamepad calls and can stop between them.
        reached: list[str] = []
        try:
            reached.append(held)
            self._pad.hold(held)
            # The held button can change the screen by itself. The second
            # input is judged on the picture it will land in, as a separate
            # press would be.
            during = self._observe_after(self._pad.timing.hold_settle_ms)
            blocked = self._first_forbidden(names, during)
            if blocked is None:
                if button is not None:
                    reached.append(button)
                    self._pad.press(button, ms)
                else:
                    self._pad.stick(stick[0], stick[1], stick[2], ms)
            self._pad.release(held)
        except BaseException as exc:
            # Nothing may stay down, and neither the cleanup nor the log may
            # replace the failure being reported.
            with contextlib.suppress(BaseException):
                self._pad.release_all()
            with contextlib.suppress(BaseException):
                self._event(FAILED, error=type(exc).__name__, reached=reached, **fields)
            raise
        if blocked is not None:
            self._event(REFUSED, reached=reached, context_held=during, **fields)
            raise ForbiddenInput(blocked, during)
        self._event(SENT, reached=reached, context_held=during, **fields)
        return context

    def _first_forbidden(self, names: list[str], context: str | None) -> str | None:
        # A combo carries no confirmation, so it never sends a button that needs one.
        forbidden = self._pack.forbidden_in(context)
        return next(
            (
                name
                for name in names
                if name in forbidden or self._confirm_where(name, context)
            ),
            None,
        )

    def _send(self, action, fields: dict) -> None:
        try:
            action()
        except Exception as exc:
            # Part of the input may have reached the device.
            self._event(FAILED, error=type(exc).__name__, **fields)
            raise
        # The gamepad call returns once the device has shown the state.
        self._event(SENT, **fields)
