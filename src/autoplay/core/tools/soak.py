import argparse
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

from autoplay.core.capture.base import CaptureError, Frame, FrameSource
from autoplay.core.capture.device import DeviceSource
from autoplay.core.controller.base import BUTTON_NAMES, Controller, ControllerError
from autoplay.core.controller.serial_backend import SerialController
from autoplay.core.runtime.estop import EmergencyStop, EStop
from autoplay.core.runtime.runlog import RunLog
from autoplay.core.tools.capture_view import parse_device_source

# Coordinates given on the command line refer to a 1920x1080 picture.
REFERENCE_SIZE = (1920, 1080)
COMPARE_SIZE = (320, 180)
FRAME_TIMEOUT_S = 1.0
# Mean absolute difference, on a 0-255 scale, between two pictures reduced to
# COMPARE_SIZE grey. Calibrated on the Switch 2 HOME menu (docs/DEVELOPMENT.md).
DEFAULT_CHANGE_THRESHOLD = 0.5
DEFAULT_SAME_THRESHOLD = 0.3

Roi = tuple[int, int, int, int]


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


def parse_roi(text: str) -> Roi:
    """Parse `x,y,w,h` in 1920x1080 coordinates; raise ValueError if it is
    malformed or not inside that picture."""
    try:
        x, y, w, h = (int(part) for part in text.split(","))
    except ValueError:
        raise ValueError(f"invalid --roi {text!r}; expected x,y,w,h") from None
    ref_w, ref_h = REFERENCE_SIZE
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > ref_w or y + h > ref_h:
        raise ValueError(f"--roi {text} is not inside {ref_w}x{ref_h}")
    return x, y, w, h


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


def signature(bgr: np.ndarray, roi: Roi | None) -> np.ndarray:
    """Reduce a frame, or the part of it inside `roi`, to a small grey
    picture that two frames can be compared by."""
    if roi is not None:
        height, width = bgr.shape[:2]
        sx, sy = width / REFERENCE_SIZE[0], height / REFERENCE_SIZE[1]
        x, y, w, h = roi
        bgr = bgr[
            round(y * sy) : round((y + h) * sy), round(x * sx) : round((x + w) * sx)
        ]
    small = cv2.resize(bgr, COMPARE_SIZE, interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)


def difference(a: Frame, b: Frame, roi: Roi | None) -> float:
    return float(np.abs(signature(a.bgr, roi) - signature(b.bgr, roi)).mean())


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
    roi: Roi | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SoakResult:
    """Press `buttons` in order, `cycles` times. Each press must change the
    picture, and each cycle must end on the picture it started from. Every
    failure is counted and its two frames are saved to `log`."""
    step_failures = cycle_failures = 0
    for cycle in range(cycles):
        start = after = fresh_frame(source)
        for step, button in enumerate(buttons):
            before = after
            pad.press(button)
            sleep(settle_s)
            after = fresh_frame(source)
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
        index = parse_device_source(args.source)
        buttons = parse_buttons(args.seq)
        roi = parse_roi(args.roi) if args.roi else None
        if args.cycles <= 0:
            raise ValueError("--cycles must be positive")
    except ValueError as exc:
        print(f"ap soak: {exc}", file=sys.stderr)
        return 2
    source = DeviceSource(index)
    try:
        source.start()
        pad = SerialController(args.port)
    except (CaptureError, ControllerError) as exc:
        source.close()
        print(f"ap soak: {exc}", file=sys.stderr)
        return 1
    estop = EStop()
    pad.attach_estop(estop)
    log = RunLog("soak")
    log.event(
        "start", port=args.port, source=args.source, seq=buttons, cycles=args.cycles
    )
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
    finally:
        pad.close()
        estop.close()
        source.close()
        log.close()
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
        "--source", default="device:0", help="frame source, device:N (default device:0)"
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
