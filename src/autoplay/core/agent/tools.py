import base64
import contextlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

from autoplay.core import clock
from autoplay.core.actions.do_until import wait_until
from autoplay.core.agent.session import Session
from autoplay.core.capture.base import CaptureError, Frame
from autoplay.core.controller.base import BUTTON_NAMES, UnknownButton
from autoplay.core.memory.lessons import Lesson
from autoplay.core.memory.notes import NotesFull
from autoplay.core.pack.guard import ForbiddenInput, NeedsConfirmation
from autoplay.core.pack.hands import Hands
from autoplay.core.pack.loader import PackError
from autoplay.core.pack.routines import Routine, RoutineStopped
from autoplay.core.perception.ops import picture_difference

FINISH = "finish"
UNKNOWN = "unknown"
MAX_PRESS_MS = 2000
MAX_STICK_MS = 2000
MAX_WAIT_MS = 5000
# On `picture_difference`: below this two pictures count as the same, above it
# as changed. The values measured for `ap soak` (docs/DEVELOPMENT.md).
SAME_THRESHOLD = 0.2
CHANGE_THRESHOLD = 0.2
_HEARTBEAT_S = 0.05
_REQUIRED = object()


@dataclass(frozen=True)
class ToolOutput:
    """What a tool hands back to the model: content blocks, and whether they
    report a failure."""

    content: list[dict]
    is_error: bool = False

    @property
    def text(self) -> str:
        return "".join(b["text"] for b in self.content if b["type"] == "text")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    run: Callable[[dict], ToolOutput]

    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class BadInput(ValueError):
    """The model called a tool with arguments it does not accept."""


def _arg(args: dict, name: str, kind: type, default=_REQUIRED, low=None, high=None):
    value = args.get(name)
    if value is None:
        if default is _REQUIRED:
            raise BadInput(f"`{name}` is required")
        return default
    if kind is float and isinstance(value, int) and not isinstance(value, bool):
        value = float(value)
    if isinstance(value, bool) is not (kind is bool) or not isinstance(value, kind):
        raise BadInput(f"`{name}` must be of type {kind.__name__}, got {value!r}")
    if (low is not None and value < low) or (high is not None and value > high):
        raise BadInput(f"`{name}` must be between {low} and {high}, got {value!r}")
    return value


def _schema(required: dict, optional: dict | None = None) -> dict:
    return {
        "type": "object",
        "properties": {**required, **(optional or {})},
        "required": list(required),
        "additionalProperties": False,
    }


def _say(**payload) -> dict:
    return {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}


def _fail(message: str) -> ToolOutput:
    return ToolOutput([{"type": "text", "text": message}], is_error=True)


def encode_frame(bgr: np.ndarray, width: int, quality: int) -> dict:
    """Return the frame as a JPEG image block, scaled down to `width` when it
    is wider."""
    height = bgr.shape[0]
    if bgr.shape[1] > width:
        height = round(height * width / bgr.shape[1])
        bgr = cv2.resize(bgr, (width, height), interpolation=cv2.INTER_AREA)
    ok, data = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise CaptureError("could not encode the frame as JPEG")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.standard_b64encode(data.tobytes()).decode("ascii"),
        },
    }


class _Toolbox:
    def __init__(self, session: Session) -> None:
        self.s = session

    def _picture(self) -> dict:
        frame = self.s.source.latest()
        if frame is None:
            raise CaptureError("the source has delivered no frame")
        config = self.s.config
        return encode_frame(frame.bgr, config.screenshot_width, config.jpeg_quality)

    def _settle(self) -> tuple[Frame, bool]:
        """Wait for the picture to stop changing and return (frame, settled).
        A picture that keeps moving, as a live scene does, is not an error."""
        previous: Frame | None = None

        def alike(frame: Frame) -> bool:
            nonlocal previous
            last, previous = previous, frame
            return (
                last is not None
                and picture_difference(last.bgr, frame.bgr) < SAME_THRESHOLD
            )

        timing, source = self.s.pad.timing, self.s.source
        frame = wait_until(
            alike,
            source=source,
            timeout=timing.settle_timeout_ms / 1000,
            stable_ms=timing.settle_stable_ms,
            heartbeat=self.s.pad.heartbeat,
        )
        if frame is not None:
            return frame, True
        latest = source.latest()
        if latest is None or source.ended() or source.stats().stale:
            raise CaptureError("the capture stopped delivering frames")
        return latest, False

    def _after_input(self, before: Frame | None, sent_in: str | None, **what):
        after, settled = self._settle()
        context = self.s.guard.context()
        changed = (
            before is not None
            and picture_difference(before.bgr, after.bgr) > CHANGE_THRESHOLD
        )
        return ToolOutput(
            [
                _say(
                    **what,
                    context_before=sent_in or UNKNOWN,
                    context_after=context or UNKNOWN,
                    picture_changed=changed,
                    settled=settled,
                ),
                self._picture(),
            ]
        )

    def look(self, args: dict) -> ToolOutput:
        context = self.s.guard.context()
        return ToolOutput([_say(context=context or UNKNOWN), self._picture()])

    def press(self, args: dict) -> ToolOutput:
        button = _arg(args, "button", str)
        ms = _arg(args, "ms", int, None, 16, MAX_PRESS_MS)
        confirm = _arg(args, "confirm", str, None)
        before = self.s.source.latest()
        sent_in = self.s.guard.press(button, ms, confirm)
        return self._after_input(before, sent_in, pressed=button)

    def hold_stick(self, args: dict) -> ToolOutput:
        which = _arg(args, "which", str)
        if which not in ("L", "R"):
            raise BadInput(f"`which` must be L or R, got {which!r}")
        x = _arg(args, "x", float, low=-1.0, high=1.0)
        y = _arg(args, "y", float, low=-1.0, high=1.0)
        ms = _arg(args, "ms", int, low=16, high=MAX_STICK_MS)
        before = self.s.source.latest()
        sent_in = self.s.guard.hold_stick(which, x, y, ms)
        return self._after_input(before, sent_in, stick=which, x=x, y=y, ms=ms)

    def combo(self, args: dict) -> ToolOutput:
        held = _arg(args, "hold", str)
        ms = _arg(args, "ms", int, low=16, high=MAX_PRESS_MS)
        button, stick = args.get("button"), args.get("stick")
        if (button is None) == (stick is None):
            raise BadInput("give exactly one of `button` and `stick`")
        move = None
        if stick is not None:
            if not isinstance(stick, dict):
                raise BadInput("`stick` must be an object with which, x and y")
            which = _arg(stick, "which", str)
            if which not in ("L", "R"):
                raise BadInput(f"`which` must be L or R, got {which!r}")
            move = (
                which,
                _arg(stick, "x", float, low=-1.0, high=1.0),
                _arg(stick, "y", float, low=-1.0, high=1.0),
            )
        elif not isinstance(button, str):
            raise BadInput("`button` must be a button name")
        before = self.s.source.latest()
        try:
            sent_in = self.s.guard.combo(held, ms, button=button, stick=move)
        except ValueError as exc:
            raise BadInput(str(exc)) from exc
        return self._after_input(before, sent_in, held=held, then=button or move)

    def routine(self, routine: Routine, args: dict) -> ToolOutput:
        """Run a pack routine. It reports text and one picture; a routine that
        stops, or meets a refused input, is an error result naming the step,
        text only.
        Whatever else it raises ends the run, with the gamepad released."""
        kwargs = routine.check_args(args)
        s = self.s
        hands = Hands(s.pack, s.guard, s.pad, s.source, s.log)
        try:
            result = routine.run(hands, **kwargs)
        except (RoutineStopped, ForbiddenInput) as stop:
            self._release()
            return self._routine_output(
                True,
                routine=routine.name,
                stopped_at=getattr(stop, "step", "") or "an input",
                reason=str(stop),
            )
        except BaseException:
            self._release()
            raise
        return self._routine_output(False, routine=routine.name, result=str(result))

    def _release(self) -> None:
        # After an emergency stop the gamepad refuses; the stop has released.
        with contextlib.suppress(Exception):
            self.s.pad.release_all()

    def _routine_output(self, is_error: bool, **what) -> ToolOutput:
        _, settled = self._settle()
        context = self.s.guard.context()
        if is_error:
            # The API accepts only text in a tool result that reports an
            # error (a 400 on 2026-11-23); the model looks for itself.
            said = _say(**what, context_after=context or UNKNOWN, settled=settled)
            return ToolOutput([said], True)
        return ToolOutput(
            [
                _say(**what, context_after=context or UNKNOWN, settled=settled),
                self._picture(),
            ],
            is_error,
        )

    def wait(self, args: dict) -> ToolOutput:
        ms = _arg(args, "ms", int, low=1, high=MAX_WAIT_MS)
        deadline = clock.now() + ms / 1000
        while True:
            self.s.pad.heartbeat()
            remaining = deadline - clock.now()
            if remaining <= 0:
                break
            time.sleep(min(_HEARTBEAT_S, remaining))
        context = self.s.guard.context()
        return ToolOutput(
            [_say(waited_ms=ms, context=context or UNKNOWN), self._picture()]
        )

    def read_guide(self, args: dict) -> ToolOutput:
        text = self.s.pack.read_chapter(_arg(args, "chapter", str))
        return ToolOutput([{"type": "text", "text": text}])

    def notes_write(self, args: dict) -> ToolOutput:
        text = _arg(args, "text", str)
        self.s.notes.write(text)
        return ToolOutput(
            [_say(written_characters=len(text), limit=self.s.notes.limit)]
        )

    def lesson_add(self, args: dict) -> ToolOutput:
        lesson = Lesson(
            slug=_arg(args, "slug", str),
            when=_arg(args, "when", str),
            steps=_arg(args, "steps", str),
            failure=_arg(args, "failure", str),
            verify=_arg(args, "verify", str),
        )
        self.s.lessons.add(lesson)
        return ToolOutput([_say(saved=lesson.slug)])

    def lesson_read(self, args: dict) -> ToolOutput:
        lesson = self.s.lessons.get(_arg(args, "slug", str))
        return ToolOutput(
            [
                _say(
                    slug=lesson.slug,
                    when=lesson.when,
                    steps=lesson.steps,
                    failure=lesson.failure,
                    verify=lesson.verify,
                    uses=lesson.uses,
                    successes=lesson.successes,
                )
            ]
        )

    def lesson_used(self, args: dict) -> ToolOutput:
        lesson = self.s.lessons.record_use(
            _arg(args, "slug", str), _arg(args, "success", bool)
        )
        return ToolOutput(
            [_say(slug=lesson.slug, uses=lesson.uses, successes=lesson.successes)]
        )

    def finish(self, args: dict) -> ToolOutput:
        _arg(args, "summary", str)
        return ToolOutput([_say(finished=True)])


def _guarded(session: Session, name: str, run: Callable[[dict], ToolOutput]):
    """Turn the failures the model can act on into error results. A stop, a
    dead gamepad link or a dead capture is not one of them: those propagate
    and end the run."""

    def call(args: dict) -> ToolOutput:
        try:
            return run(args)
        except NeedsConfirmation as exc:
            session.log.event(
                "needs_confirmation", tool=name, button=exc.button, where=exc.context
            )
            return _fail(f"Not sent: {exc}.")
        except ForbiddenInput as exc:
            session.log.event(
                "forbidden_input", tool=name, button=exc.button, context=exc.context
            )
            return _fail(
                f"Refused: {exc}. Nothing was sent to the gamepad. This button is "
                "blocked here on purpose; do not look for another way to press it."
            )
        except KeyError as exc:
            return _fail(str(exc.args[0]))
        except (BadInput, UnknownButton, NotesFull, PackError, ValueError) as exc:
            return _fail(str(exc))

    return call


def build_tools(session: Session) -> list[Tool]:
    """The tools of one run, in a fixed order. `finish` is among them; the
    loop ends the run when it is called."""
    box = _Toolbox(session)
    text, integer, number = {"type": "string"}, {"type": "integer"}, {"type": "number"}
    button = {"type": "string", "enum": list(BUTTON_NAMES)}
    picture = (
        "Returns the context the picture was recognised as (`unknown` when it "
        "matches none) and a screenshot."
    )
    after = (
        "Then waits for the picture to settle and returns the context before and "
        "after, whether the picture changed, and a screenshot. `settled: false` "
        "means the picture was still moving, which is normal in a live scene."
    )
    specs = [
        (
            "look",
            f"Look at the screen without touching the gamepad. {picture}",
            _schema({}),
            box.look,
        ),
        (
            "press",
            (
                "Press one gamepad button and release it. `ms` is how long it is held "
                f"(default: a short tap; at most {MAX_PRESS_MS}). {after} A button the "
                "current context forbids is refused and not sent. Where the game asks "
                "you to choose, the button is sent only with `confirm`: the text of "
                "the option you read on screen and mean to choose."
            ),
            _schema({"button": button}, {"ms": integer, "confirm": text}),
            box.press,
        ),
        (
            "hold_stick",
            (
                "Deflect a stick (`which`: L or R) to x, y in [-1, 1], +y up, for `ms` "
                f"milliseconds (at most {MAX_STICK_MS}), then centre it. {after}"
            ),
            _schema(
                {
                    "which": {"type": "string", "enum": ["L", "R"]},
                    "x": number,
                    "y": number,
                    "ms": integer,
                }
            ),
            box.hold_stick,
        ),
        (
            "combo",
            (
                "Hold one button (`hold`), and while it is held press `button` or "
                "deflect `stick` ({which, x, y}) for `ms` milliseconds "
                f"(at most {MAX_PRESS_MS}); then release it. Give exactly one "
                f"of `button` and `stick`. {after} A button the context forbids is "
                "refused; the context is read again once `hold` is down."
            ),
            _schema(
                {"hold": button, "ms": integer},
                {
                    "button": button,
                    "stick": {
                        "type": "object",
                        "properties": {
                            "which": {"type": "string", "enum": ["L", "R"]},
                            "x": number,
                            "y": number,
                        },
                        "required": ["which", "x", "y"],
                    },
                },
            ),
            box.combo,
        ),
        (
            "wait",
            f"Do nothing for `ms` milliseconds (at most {MAX_WAIT_MS}). {picture}",
            _schema({"ms": integer}),
            box.wait,
        ),
        (
            "read_guide",
            (
                "Read one chapter of the game guide, by the chapter name listed in the "
                "system prompt."
            ),
            _schema({"chapter": text}),
            box.read_guide,
        ),
        (
            "notes_write",
            (
                "Replace the progress notes with `text`. The notes are given to you at "
                "the start of every run and are limited in length; a text over the "
                "limit is refused, not cut short."
            ),
            _schema({"text": text}),
            box.notes_write,
        ),
        (
            "lesson_add",
            (
                "Save a draft of something you learned that a later run can reuse. "
                "`slug`: lower-case words joined by hyphens. `when`: the situation it "
                "applies to, one line. `steps`: what to do. `failure`: what to do when "
                "it goes wrong. `verify`: how to tell it worked."
            ),
            _schema(
                {
                    "slug": text,
                    "when": text,
                    "steps": text,
                    "failure": text,
                    "verify": text,
                }
            ),
            box.lesson_add,
        ),
        (
            "lesson_read",
            "Read a lesson draft in full, by its slug.",
            _schema({"slug": text}),
            box.lesson_read,
        ),
        (
            "lesson_used",
            "Record that you applied a lesson draft and whether it worked.",
            _schema({"slug": text, "success": {"type": "boolean"}}),
            box.lesson_used,
        ),
        (
            FINISH,
            (
                "End the run. `summary`: what was achieved, what was not, and the "
                "context the game was left in."
            ),
            _schema({"summary": text}),
            box.finish,
        ),
    ]
    taken = {name for name, _, _, _ in specs}
    for routine in session.pack.routines.values():
        if routine.name in taken:
            raise PackError(
                f"pack {session.pack.name}: routine {routine.name!r} has the name of "
                "a built-in tool"
            )
        specs.append(
            (
                routine.name,
                (
                    f"Routine of this game: {routine.description} It checks the picture "
                    "at its own steps and reports the result with one screenshot; if "
                    "it stops, it says at which step and why, without a screenshot: "
                    "call look."
                ),
                _schema({key: param.schema() for key, param in routine.params.items()}),
                lambda args, routine=routine: box.routine(routine, args),
            )
        )
    return [
        Tool(name, description, schema, _guarded(session, name, run))
        for name, description, schema, run in specs
    ]
