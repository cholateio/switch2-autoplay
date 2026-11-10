import argparse
import math
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from autoplay.core import clock
from autoplay.core.capture.base import CaptureError, FrameSource
from autoplay.core.capture.device import DeviceSource, fourcc_str, open_capture
from autoplay.core.tools.monitors import Monitor, format_monitors, list_monitors
from autoplay.core.tools.pattern import (
    decode_stamp,
    encode_stamp,
    paint_stamp,
    stamp_age_ms,
)

PROBE_BACKENDS = ("dshow", "msmf")
PROBE_FOURCCS = ("YUY2", "NV12", "MJPG")
PROBE_SECONDS = 5.0
LOOPBACK_SECONDS = 10.0
# The first frames after the window opens still show the previous screen.
LOOPBACK_WARMUP_SECONDS = 1.0
MIN_DECODE_RATE = 0.9
# A run must receive at least this fraction of LOOPBACK_SECONDS x the source fps.
MIN_RECEIVED_FRACTION = 0.5
# A stamp corrupted in two cells still passes parity and yields an age near
# 2**24 ms; any age above this bound counts as a decode failure.
MAX_AGE_MS = 2000
_READ_FAIL_BACKOFF_S = 0.005


@dataclass(frozen=True)
class ReadTiming:
    fps: float
    p50_ms: float
    p99_ms: float
    read_ms: float


@dataclass(frozen=True)
class LoopbackRun:
    ages_ms: list[int]
    received: int
    source_fps: float
    error: str | None


@dataclass(frozen=True)
class LoopbackSummary:
    p50_ms: float
    p99_ms: float
    decoded: int
    received: int
    min_frames: int
    decode_rate: float
    valid: bool
    reason: str | None


def interval_percentiles_ms(times_s: Sequence[float]) -> tuple[float, float]:
    """Return p50 and p99 of consecutive intervals in ms; NaN with fewer than two times."""
    if len(times_s) < 2:
        return math.nan, math.nan
    gaps = np.diff(np.asarray(times_s, dtype=np.float64)) * 1000
    p50, p99 = np.percentile(gaps, [50, 99])
    return float(p50), float(p99)


def summarize_reads(
    read_ends: Sequence[float], read_durations: Sequence[float]
) -> ReadTiming:
    """Summarize successful `read()` calls from their completion times and durations
    in seconds; fps spans the first to the last completion only."""
    p50, p99 = interval_percentiles_ms(read_ends)
    span = read_ends[-1] - read_ends[0] if len(read_ends) >= 2 else 0.0
    fps = (len(read_ends) - 1) / span if span > 0 else math.nan
    read_ms = float(np.mean(read_durations)) * 1000 if len(read_durations) else math.nan
    return ReadTiming(fps, p50, p99, read_ms)


def summarize_loopback(
    ages_ms: Sequence[int],
    received: int,
    source_fps: float,
    seconds: float = LOOPBACK_SECONDS,
) -> LoopbackSummary:
    """Judge a loopback run. Ages above MAX_AGE_MS count as decode failures. The run
    is valid only if source_fps is positive, it received at least
    MIN_RECEIVED_FRACTION x seconds x source_fps frames and it decoded at least
    MIN_DECODE_RATE of them."""
    plausible = [age for age in ages_ms if age <= MAX_AGE_MS]
    rate = len(plausible) / received if received else 0.0
    min_frames = math.ceil(MIN_RECEIVED_FRACTION * seconds * source_fps)
    if plausible:
        p50, p99 = (float(v) for v in np.percentile(plausible, [50, 99]))
    else:
        p50 = p99 = math.nan
    reason = None
    if source_fps <= 0:
        reason = "source reported 0 fps when the measurement started"
    elif received < min_frames:
        reason = (
            f"received {received} frames, need at least {min_frames} "
            f"({MIN_RECEIVED_FRACTION:.0%} of {seconds:g} s x {source_fps:.1f} fps)"
        )
    elif rate < MIN_DECODE_RATE:
        reason = f"decode rate {rate:.1%} below {MIN_DECODE_RATE:.0%}"
    return LoopbackSummary(
        p50, p99, len(plausible), received, min_frames, rate, reason is None, reason
    )


class AgeCollector:
    """Decodes each new frame of `src` whose `ts` lies in [measure_from,
    measure_until] and records its age in ms. `run(done)` returns when `done` is
    set or on the first exception, whose text it stores in `error`."""

    def __init__(
        self, src: FrameSource, measure_from: float, measure_until: float
    ) -> None:
        self._src = src
        self._from = measure_from
        self._until = measure_until
        self.ages_ms: list[int] = []
        self.received = 0
        self.error: str | None = None

    def run(self, done: threading.Event) -> None:
        try:
            seq = 0
            while not done.is_set():
                frame = self._src.wait_next(seq, 0.1)
                if frame is None:
                    continue
                seq = frame.seq
                if not self._from <= frame.ts <= self._until:
                    continue
                self.received += 1
                stamp = decode_stamp(frame.bgr)
                if stamp is not None:
                    # Same clock as the drawn stamp: both come from clock.now().
                    self.ages_ms.append(stamp_age_ms(int(frame.ts * 1000), stamp))
        # Any failure must end the run visibly instead of leaving a silently short sample.
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"


def _brief(exc: Exception) -> str:
    text = str(exc).strip().splitlines()
    first = text[0] if text else type(exc).__name__
    return first if len(first) <= 80 else first[:77] + "..."


def _time_reads(index, backend, fourcc, opener, seconds):
    cap = open_capture(
        index,
        backend=backend,
        width=1920,
        height=1080,
        fps=60,
        fourcc=fourcc,
        opener=opener,
    )
    try:
        cap.read()
        ends: list[float] = []
        durations: list[float] = []
        shape = None
        deadline = clock.now() + seconds
        while clock.now() < deadline:
            t0 = clock.now()
            ok, img = cap.read()
            t1 = clock.now()
            if not ok or img is None:
                time.sleep(_READ_FAIL_BACKOFF_S)
                continue
            ends.append(t1)
            durations.append(t1 - t0)
            shape = img.shape
        actual = fourcc_str(cap.get(cv2.CAP_PROP_FOURCC))
    finally:
        cap.release()
    return ends, durations, shape, actual


def _probe_one(index: int, backend: str, fourcc: str, opener, seconds: float) -> str:
    label = f"{backend:<7} {fourcc:<6}"
    try:
        ends, durations, shape, actual = _time_reads(
            index, backend, fourcc, opener, seconds
        )
    # One failing combination (cv2.error included) must not stop the table.
    except Exception as exc:  # noqa: BLE001
        return f"{label} unavailable ({_brief(exc)})"
    if shape is None:
        return f"{label} unavailable (no frames)"
    t = summarize_reads(ends, durations)
    size = f"{shape[1]}x{shape[0]}"
    return (
        f"{label} {size:<10} {actual:<6} {t.fps:>6.2f} {t.p50_ms:>7.2f} "
        f"{t.p99_ms:>7.2f} {t.read_ms:>8.2f}"
    )


def _run_table(
    index: int,
    *,
    opener: Callable[..., "cv2.VideoCapture"] | None = None,
    seconds: float = PROBE_SECONDS,
) -> int:
    print("backend fourcc size       actual    fps  p50 ms  p99 ms  read ms")
    for backend in PROBE_BACKENDS:
        for fourcc in PROBE_FOURCCS:
            print(_probe_one(index, backend, fourcc, opener, seconds), flush=True)
    return 0


def _measure_loopback(src: DeviceSource, monitor: Monitor) -> LoopbackRun:
    window = "ap probe loopback"
    measure_from = clock.now() + LOOPBACK_WARMUP_SECONDS
    measure_until = measure_from + LOOPBACK_SECONDS
    collector = AgeCollector(src, measure_from, measure_until)
    done = threading.Event()
    source_fps: float | None = None
    img = encode_stamp(0, (monitor.width, monitor.height))
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.moveWindow(window, monitor.x, monitor.y)
    cv2.setWindowProperty(window, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    consumer = threading.Thread(
        target=collector.run, args=(done,), name="loopback-decode"
    )
    consumer.start()
    try:
        while (now := clock.now()) < measure_until and consumer.is_alive():
            if source_fps is None and now >= measure_from:
                source_fps = src.stats().fps
            # imshow copies into the window buffer, so repainting `img` in place is safe.
            paint_stamp(img, int(clock.now() * 1000))
            cv2.imshow(window, img)
            cv2.waitKey(1)
    finally:
        done.set()
        consumer.join()
        cv2.destroyWindow(window)
    return LoopbackRun(
        collector.ages_ms, collector.received, source_fps or 0.0, collector.error
    )


def _run_loopback(index: int, monitor_index: int | None) -> int:
    try:
        monitors = list_monitors()
    except (RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(format_monitors(monitors))
    if monitor_index is None:
        print(
            "error: pass --monitor N for the output wired to the capture card",
            file=sys.stderr,
        )
        return 2
    if not 0 <= monitor_index < len(monitors):
        print(
            f"error: no monitor {monitor_index}; choose one listed above",
            file=sys.stderr,
        )
        return 2
    monitor = monitors[monitor_index]
    src = DeviceSource(index)
    try:
        src.start()
    except CaptureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    with src:
        run = _measure_loopback(src, monitor)
    summary = summarize_loopback(run.ages_ms, run.received, run.source_fps)
    print(
        f"monitor {monitor.index} refresh {monitor.refresh_hz} Hz; source {run.source_fps:.1f} "
        f"fps; decoded {summary.decoded}/{summary.received} frames ({summary.decode_rate:.1%})"
    )
    if run.error is not None:
        print(f"measurement failed: {run.error}", file=sys.stderr)
        return 1
    if not summary.valid:
        print(f"measurement invalid: {summary.reason}", file=sys.stderr)
        return 1
    print(
        f"frame age p50 {summary.p50_ms:.1f} ms, p99 {summary.p99_ms:.1f} ms "
        "(upper bound: includes draw-to-output delay)"
    )
    return 0


def _handle(args: argparse.Namespace) -> int:
    if args.loopback:
        return _run_loopback(args.index, args.monitor)
    if args.monitor is not None:
        print("error: --monitor only applies with --loopback", file=sys.stderr)
        return 2
    return _run_table(args.index)


def register(sub) -> None:
    p = sub.add_parser(
        "probe",
        help="measure what a capture device delivers",
        description=(
            "Without --loopback: open every backend x pixel format for "
            f"{PROBE_SECONDS:.0f} s and print size, format, fps and frame timing. "
            "With --loopback: show a time-stamp pattern full screen on --monitor N "
            "and measure how old captured frames are."
        ),
    )
    p.add_argument(
        "--index", type=int, default=0, help="capture device index (default 0)"
    )
    p.add_argument("--loopback", action="store_true", help="measure captured-frame age")
    p.add_argument(
        "--monitor", type=int, help="monitor wired to the capture card (loopback)"
    )
    p.set_defaults(func=_handle)
