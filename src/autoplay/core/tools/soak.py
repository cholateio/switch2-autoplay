import argparse
import contextlib
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

from autoplay.core.capture.base import CaptureError, Frame, FrameSource
from autoplay.core.capture.factory import SOURCE_FORMS, open_source
from autoplay.core.controller.base import BUTTON_NAMES, Controller, ControllerError
from autoplay.core.controller.serial_backend import SerialController
from autoplay.core.perception.geometry import ROI, parse_roi
from autoplay.core.perception.ops import picture_difference
from autoplay.core.runtime.estop import EmergencyStop, EStop
from autoplay.core.runtime.runlog import RunLog

FRAME_TIMEOUT_S = 1.0
# Thresholds on `perception.ops.picture_difference`. Measured on the Switch 2
# HOME menu: the smallest real change (the cursor moving one icon in the bottom row) is 0.33 to 0.37, and
# the same picture taken twice differs by 0.07 to 0.11 (docs/DEVELOPMENT.md).
DEFAULT_CHANGE_THRESHOLD = 0.2
DEFAULT_SAME_THRESHOLD = 0.2


@dataclass(frozen=True)
class SoakResult:
    cycles: int
    step_failures: int
    cycle_failures: int

    @property
    def ok(self) -> bool:
        return self.step_failures == 0 and self.cycle_failures == 0

    def __str__(self) -> str:
        return (
            f"cycles={self.cycles} step_failures={self.step_failures} "
            f"cycle_failures={self.cycle_failures}"
        )


def parse_buttons(text: str) -> list[str]:
    """Split a comma-separated button list; raise ValueError naming a button
    that does not exist."""
    names = [part.strip() for part in text.split(",")]
    for name in names:
        if name not in BUTTON_NAMES:
            raise ValueError(
                f"unknown button {name!r}; expected one of {', '.join(BUTTON_NAMES)}"
            )
    return names


def difference(a: Frame, b: Frame, roi: ROI | None) -> float:
    return picture_difference(a.bgr, b.bgr, roi)


def fresh_frame(source: FrameSource) -> Frame:
    """Return a frame captured after this call; raise CaptureError if none
    arrives."""
    latest = source.latest()
    frame = source.wait_next(latest.seq if latest else 0, FRAME_TIMEOUT_S)
    if frame is None:
        raise CaptureError(
            f"no new frame within {FRAME_TIMEOUT_S} s; the capture has stalled"
        )
    return frame


def run_soak(
    pad: Controller,
    source: FrameSource,
    buttons: list[str],
    cycles: int,
    log: RunLog,
    *,
    settle_s: float,
    change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
    same_threshold: float = DEFAULT_SAME_THRESHOLD,
    roi: ROI | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SoakResult:
    """Press `buttons` in order, `cycles` times. Each press must change the
    picture, and each cycle must end on the picture it started from. Every
    failure is counted and its two frames are saved to `log`.

    Raises `EmergencyStop` or `ControllerError` as soon as the controller
    reports one, also while waiting between presses."""
    step_failures = cycle_failures = 0
    for cycle in range(cycles):
        start = after = fresh_frame(source)
        for step, button in enumerate(buttons):
            before = after
            pad.press(button)
            sleep(settle_s)
            after = fresh_frame(source)
            # Nothing else talks to the controller during the wait: without
            # this a stop or a dead link in the last wait would go unseen and
            # the run would be reported as a success.
            pad.heartbeat()
            changed = difference(before, after, roi)
            ok = changed > change_threshold
            log.event(
                "step",
                cycle=cycle,
                step=step,
                button=button,
                diff=round(changed, 3),
                ok=ok,
            )
            if not ok:
                step_failures += 1
                log.save_frame(f"c{cycle}-s{step}-{button}-before", before)
                log.save_frame(f"c{cycle}-s{step}-{button}-after", after)
        drift = difference(start, after, roi)
        ok = drift < same_threshold
        log.event("cycle", cycle=cycle, diff=round(drift, 3), ok=ok)
        if not ok:
            cycle_failures += 1
            log.save_frame(f"c{cycle}-start", start)
            log.save_frame(f"c{cycle}-end", after)
    return SoakResult(cycles, step_failures, cycle_failures)


def _handle(args: argparse.Namespace) -> int:
    try:
        source = open_source(args.source)
        buttons = parse_buttons(args.seq)
        roi = parse_roi(args.roi) if args.roi else None
        if args.cycles <= 0:
            raise ValueError("--cycles must be positive")
    except ValueError as exc:
        print(f"ap soak: {exc}", file=sys.stderr)
        return 2
    # Each resource is registered for closing as soon as it exists, so a
    # failure while opening a later one still releases the earlier ones.
    with contextlib.ExitStack() as stack:
        try:
            stack.callback(source.close)
            source.start()
            pad = SerialController(args.port)
            stack.callback(pad.close)
            estop = EStop()
            stack.callback(estop.close)
            pad.attach_estop(estop)
            log = RunLog("soak")
            stack.callback(log.close)
            log.event(
                "start",
                port=args.port,
                source=args.source,
                seq=buttons,
                cycles=args.cycles,
            )
        except (CaptureError, ControllerError, OSError) as exc:
            print(f"ap soak: {exc}", file=sys.stderr)
            return 1
        print(f"ap soak: logging to {log.dir}; F12 stops", file=sys.stderr)
        try:
            result = run_soak(
                pad,
                source,
                buttons,
                args.cycles,
                log,
                settle_s=args.settle_ms / 1000,
                change_threshold=args.change_threshold,
                same_threshold=args.same_threshold,
                roi=roi,
            )
        except EmergencyStop:
            log.event("emergency_stop")
            print("ap soak: emergency stop", file=sys.stderr)
            return 3
        except (CaptureError, ControllerError) as exc:
            log.event("error", message=str(exc))
            print(f"ap soak: {exc}", file=sys.stderr)
            return 1
    print(result)
    return 0 if result.ok else 1


def register(sub) -> None:
    p = sub.add_parser(
        "soak",
        help="press a button sequence many times and check the picture each time",
        description=(
            "Each press must change the picture, and each cycle must end on the picture "
            "it started from. Failures are counted and their frames saved under runs/. "
            "F12 is the emergency stop."
        ),
    )
    p.add_argument(
        "--port", required=True, help="serial port of the firmware, such as COM4"
    )
    p.add_argument(
        "--source",
        default="device:0",
        help=f"frame source: {SOURCE_FORMS} (default device:0)",
    )
    p.add_argument(
        "--seq", required=True, help="comma-separated buttons pressed in each cycle"
    )
    p.add_argument("--cycles", type=int, default=100)
    p.add_argument(
        "--settle-ms",
        type=int,
        default=400,
        help="wait after each press before looking",
    )
    p.add_argument("--roi", help="compare only x,y,w,h (1920x1080 coordinates)")
    p.add_argument(
        "--change-threshold",
        type=float,
        default=DEFAULT_CHANGE_THRESHOLD,
        help="a press must change the picture by more than this",
    )
    p.add_argument(
        "--same-threshold",
        type=float,
        default=DEFAULT_SAME_THRESHOLD,
        help="a cycle must end within this of where it started",
    )
    p.set_defaults(func=_handle)
