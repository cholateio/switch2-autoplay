import argparse
import contextlib
import math
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

from autoplay.core import clock
from autoplay.core.actions.do_until import TIMEOUT, ActionFailed, do_until
from autoplay.core.capture.base import CaptureError, FrameSource
from autoplay.core.capture.device import DeviceSource
from autoplay.core.capture.factory import open_source
from autoplay.core.controller.base import BUTTON_NAMES, Controller, ControllerError
from autoplay.core.controller.serial_backend import SerialController
from autoplay.core.perception.geometry import ROI, parse_roi
from autoplay.core.runtime.estop import EmergencyStop, EStop
from autoplay.core.runtime.runlog import RunLog
from autoplay.core.tools.soak import (
    DEFAULT_CHANGE_THRESHOLD,
    DEFAULT_SAME_THRESHOLD,
    difference,
    fresh_frame,
)


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile, q in (0, 1]: the smallest value that at least
    q of the values do not exceed."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


@dataclass(frozen=True)
class LatencyResult:
    """`samples_ms`: time from the call that presses the button to the
    timestamp of the first frame that shows a change. `missed`: presses no
    change followed. `drifted`: times the undo press did not bring the
    picture back."""

    samples_ms: tuple[float, ...]
    missed: int
    drifted: int

    @property
    def ok(self) -> bool:
        return self.missed == 0 and len(self.samples_ms) > 0

    def __str__(self) -> str:
        head = (
            f"n={len(self.samples_ms) + self.missed} measured={len(self.samples_ms)} "
            f"missed={self.missed} drifted={self.drifted}"
        )
        if not self.samples_ms:
            return head
        values = list(self.samples_ms)
        return (
            f"{head} p50={percentile(values, 0.5):.1f} p90={percentile(values, 0.9):.1f} "
            f"max={max(values):.1f} min={min(values):.1f} ms"
        )


def run_latency(
    pad: Controller,
    source: FrameSource,
    button: str,
    undo: str,
    n: int,
    log: RunLog,
    *,
    settle_s: float,
    timeout: float = 1.0,
    change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
    same_threshold: float = DEFAULT_SAME_THRESHOLD,
    roi: ROI | None = None,
    now: Callable[[], float] = clock.now,
    sleep: Callable[[float], None] = time.sleep,
) -> LatencyResult:
    """Measure `n` times how long after `button` goes down the picture
    changes, pressing `undo` after each to go back.

    The button is held until the change is seen and is never pressed a second
    time for one sample. Raises `CaptureError` when the capture stops, and
    `EmergencyStop` or `ControllerError` as the controller reports them."""
    samples: list[float] = []
    missed = drifted = 0
    for i in range(n):
        base = fresh_frame(source)
        sent: list[float] = []

        def press_down(sent: list[float] = sent) -> None:
            sent.append(now())
            pad.hold(button)

        try:
            frame = do_until(
                press_down,
                lambda f, base=base: difference(base, f, roi) > change_threshold,
                source=source,
                timeout=timeout,
                retries=0,
                stable_ms=0,
                name=f"sample{i}",
                runlog=log,
                heartbeat=pad.heartbeat,
            )
        except ActionFailed as exc:
            pad.release(button)
            log.event("missed", i=i, reason=exc.reason)
            if exc.reason != TIMEOUT:
                raise CaptureError(f"cannot measure: {exc.reason}") from exc
            missed += 1
        else:
            pad.release(button)
            ms = (frame.ts - sent[0]) * 1000
            samples.append(ms)
            log.event("sample", i=i, ms=round(ms, 2))
        sleep(settle_s)
        pad.heartbeat()
        pad.press(undo)
        sleep(settle_s)
        pad.heartbeat()
        drift = difference(base, fresh_frame(source), roi)
        if drift >= same_threshold:
            drifted += 1
            log.event("drift", i=i, diff=round(drift, 3))
    return LatencyResult(tuple(samples), missed, drifted)


def _handle(args: argparse.Namespace) -> int:
    try:
        for name in (args.button, args.undo):
            if name not in BUTTON_NAMES:
                raise ValueError(
                    f"unknown button {name!r}; expected one of {', '.join(BUTTON_NAMES)}"
                )
        source = open_source(args.source)
        # A file's frame times are media time and its frames cannot react to
        # the press, so a "latency" from one would be meaningless.
        if not isinstance(source, DeviceSource):
            raise ValueError(  # noqa: TRY004 - a usage error like the others here
                "--source must be a capture device (device:<index>)"
            )
        roi = parse_roi(args.roi) if args.roi else None
        if args.n <= 0:
            raise ValueError("--n must be positive")
    except ValueError as exc:
        print(f"ap latency: {exc}", file=sys.stderr)
        return 2
    with contextlib.ExitStack() as stack:
        try:
            stack.callback(source.close)
            source.start()
            pad = SerialController(args.port)
            stack.callback(pad.close)
            estop = EStop()
            stack.callback(estop.close)
            pad.attach_estop(estop)
            log = RunLog("latency")
            stack.callback(log.close)
            log.event(
                "start",
                port=args.port,
                source=args.source,
                button=args.button,
                undo=args.undo,
                n=args.n,
            )
        except (CaptureError, ControllerError, OSError) as exc:
            print(f"ap latency: {exc}", file=sys.stderr)
            return 1
        print(f"ap latency: logging to {log.dir}; F12 stops", file=sys.stderr)
        try:
            result = run_latency(
                pad,
                source,
                args.button,
                args.undo,
                args.n,
                log,
                settle_s=args.settle_ms / 1000,
                timeout=args.timeout_ms / 1000,
                change_threshold=args.change_threshold,
                roi=roi,
            )
        except EmergencyStop:
            log.event("emergency_stop")
            print("ap latency: emergency stop", file=sys.stderr)
            return 3
        except (CaptureError, ControllerError) as exc:
            log.event("error", message=str(exc))
            print(f"ap latency: {exc}", file=sys.stderr)
            return 1
        log.event("result", summary=str(result))
    print(result)
    return 0 if result.ok else 1


def register(sub) -> None:
    p = sub.add_parser(
        "latency",
        help="measure the time from a button press to the picture changing",
        description=(
            "Holds --button until the picture changes, then presses --undo to go back, "
            "--n times, and prints p50/p90/max in ms. The time runs from the call that "
            "presses the button to the timestamp of the first changed frame, so it "
            "includes the capture's own delay. F12 is the emergency stop."
        ),
    )
    p.add_argument(
        "--port", required=True, help="serial port of the firmware, such as COM4"
    )
    p.add_argument(
        "--source",
        default="device:0",
        help="capture device, device:<index> (default device:0)",
    )
    p.add_argument("--button", required=True, help="button whose effect is timed")
    p.add_argument("--undo", required=True, help="button that reverses it")
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--roi", help="compare only x,y,w,h (1920x1080 coordinates)")
    p.add_argument(
        "--settle-ms",
        type=int,
        default=400,
        help="wait after each release and each undo press",
    )
    p.add_argument(
        "--timeout-ms",
        type=int,
        default=1000,
        help="give up on one press after this long",
    )
    p.add_argument(
        "--change-threshold",
        type=float,
        default=DEFAULT_CHANGE_THRESHOLD,
        help="the picture has changed when it differs by more than this",
    )
    p.set_defaults(func=_handle)
